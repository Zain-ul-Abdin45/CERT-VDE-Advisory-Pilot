#!/usr/bin/env python3
"""
extract_report.py — local OCR extractor for TÜV NORD "Auditbericht" PDFs
(form template A00F207) into the same JSON schema as Auditbericht.json.

Runs entirely locally: PyMuPDF renders each page to an image, OpenCV strips
the table grid lines (which otherwise confuse OCR badly), Tesseract OCRs the
cleaned image, and a set of regex/heuristic parsers map the recognized text
onto the report's known field structure. Nothing is uploaded anywhere.

Usage:
    python3 extract_report.py <input.pdf> [output.json] [--debug]

--debug   also writes <output>_debug/ with the cleaned page images and raw
          OCR text per page, so you can spot-check anything that looks off.

Notes / limitations (read before trusting the output blindly):
  - This PDF has no text layer (every page is a flattened raster image), so
    everything below depends on OCR quality. Narrative paragraphs and plain
    label/value fields OCR very reliably; checkbox states are inherently the
    least reliable part (they're recovered from a text heuristic — an 'X'
    character immediately before an option's label — not pixel inspection),
    so double-check any boolean flag that matters before relying on it.
  - The ISO 9001:2015 / ISO 14001:2015 chapter numbering in the Auditergebnis
    table is taken from the published standards (not re-derived from OCR),
    because OCR frequently mangles the small "4.1"-style digits. Only the
    per-chapter result score (0-3/-) is read from the page. If a future
    report uses a different standard in those extra columns, that column's
    results are left as raw OCR text instead.
  - Built and tuned against one real report of this template. New files
    using the same template should work, but if TÜV changes the form layout
    materially, the section parsers may need adjusting.
"""

import sys
import re
import os
import json
import datetime
import argparse

import fitz  # PyMuPDF
import cv2
import numpy as np
import pytesseract

DPI = 300
LANG = "deu"

ISO_9001_2015_CHAPTERS = [
    "4.1", "4.2", "4.3", "4.4",
    "5.1", "5.2", "5.3",
    "6.1", "6.2", "6.3",
    "7.1", "7.2", "7.3", "7.4", "7.5",
    "8.1", "8.2", "8.3", "8.4", "8.5", "8.6", "8.7",
    "9.1", "9.2", "9.3",
    "10.1", "10.2", "10.3",
]

ISO_14001_2015_CHAPTERS = [
    "4.1", "4.2", "4.3", "4.4",
    "5.1", "5.2", "5.3",
    "6.1", "6.2",
    "7.1", "7.2", "7.3", "7.4", "7.5",
    "8.1", "8.2",
    "9.1", "9.2", "9.3",
    "10.1", "10.2", "10.3",
]

VALID_ERG_TOKENS = {"0", "1", "2", "3", "-"}


# --------------------------------------------------------------------------
# low-level image / OCR utilities
# --------------------------------------------------------------------------

def render_page_gray(doc, page_no, dpi=DPI):
    pix = doc[page_no].get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2RGB)
    if pix.n >= 3:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    else:
        gray = arr[:, :, 0]
    return gray


def detect_line_masks(gray, axis_frac=30):
    bw = cv2.adaptiveThreshold(~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
    h_struct = cv2.getStructuringElement(cv2.MORPH_RECT, (max(gray.shape[1] // axis_frac, 1), 1))
    horizontal = cv2.dilate(cv2.erode(bw.copy(), h_struct), h_struct)
    v_struct = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(gray.shape[0] // axis_frac, 1)))
    vertical = cv2.dilate(cv2.erode(bw.copy(), v_struct), v_struct)
    return horizontal, vertical


def remove_grid_lines(gray):
    """Table borders confuse Tesseract badly; painting them white before OCR
    fixes the large majority of dropped/garbled cells on this form."""
    horizontal, vertical = detect_line_masks(gray)
    mask = cv2.bitwise_or(horizontal, vertical)
    cleaned = gray.copy()
    cleaned[mask > 0] = 255
    return cleaned, vertical


def vertical_line_centers(vertical_mask, min_height_frac=0.15):
    thresh = vertical_mask.shape[0] * min_height_frac
    col_sums = (vertical_mask > 0).sum(axis=0)
    xs = np.where(col_sums > thresh)[0]
    if len(xs) == 0:
        return []
    groups = []
    cur = [xs[0]]
    for x in xs[1:]:
        if x - cur[-1] <= 5:
            cur.append(x)
        else:
            groups.append(cur)
            cur = [x]
    groups.append(cur)
    centers = [int(np.mean(g)) for g in groups]

    # Two "lines" this close together are essentially always the same rule
    # detected twice (anti-aliasing, a short tick mark) rather than a real
    # extra column — a genuine table column is at least ~100px wide on this
    # template. Left uncollapsed, a spurious extra center shifts every
    # column index after it by one.
    merged = []
    for c in centers:
        if merged and c - merged[-1] < 40:
            merged[-1] = (merged[-1] + c) // 2
        else:
            merged.append(c)
    return merged


def ink_ratio(gray, x0, y0, x1, y1, inset=10):
    """Fraction of dark pixels inside a cropped cell, after insetting past the
    cell border. Used to detect checkbox fill state directly from pixels
    rather than depending on OCR to read a ~20px glyph correctly."""
    x0i, y0i, x1i, y1i = x0 + inset, y0 + inset, x1 - inset, y1 - inset
    if x1i <= x0i or y1i <= y0i:
        return 0.0
    crop = gray[y0i:y1i, x0i:x1i]
    if crop.size == 0:
        return 0.0
    bw = cv2.adaptiveThreshold(~crop, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
    return float((bw > 0).mean())


def ocr_text(gray_img, psm=4, lang=LANG):
    return pytesseract.image_to_string(gray_img, lang=lang, config=f"--psm {psm}")


def ocr_words(gray_img, psm=6, lang=LANG):
    data = pytesseract.image_to_data(gray_img, lang=lang, config=f"--psm {psm}",
                                      output_type=pytesseract.Output.DICT)
    words = []
    for i in range(len(data["text"])):
        t = data["text"][i].strip()
        if not t:
            continue
        words.append({
            "text": t,
            "x": data["left"][i], "y": data["top"][i],
            "w": data["width"][i], "h": data["height"][i],
        })
    return words


# Grid-line removal leaves faint remnants near cell edges that Tesseract
# occasionally turns into stray leading punctuation (|, ', ‚, comma, dot).
_LEADING_NOISE = re.compile(r"^[|'\"‚.,:;·•]+\s*")
_TRAILING_NOISE = re.compile(r"\s*[|'\"‚·•,]+$")


def _strip_noise(s):
    if s is None:
        return None
    s = _LEADING_NOISE.sub("", s)
    s = _TRAILING_NOISE.sub("", s).strip()
    return s or None


def clean_lines(text):
    return [_LEADING_NOISE.sub("", ln.rstrip()) for ln in text.split("\n") if ln.strip()]


# --------------------------------------------------------------------------
# checkbox text heuristic
# --------------------------------------------------------------------------
#
# Checkboxes on this form are Unicode-glyph checkmarks (rendered visually,
# no text layer) that Tesseract reads inconsistently: a checked box comes
# through as an uppercase 'X' plus assorted stray letters glued to it
# ("X]", "IX]", "DX]", "&X]", "RX"); an unchecked box comes through as
# bracket/letter noise with no 'X' ("[_]", "[]", "D", "U", "L_]"). Matching
# is restricted to uppercase 'X' only (not lowercase 'x') because several
# German labels contain a lowercase x organically (e.g. "WebEx"), which
# would otherwise false-positive.

LABEL_ALIASES = {
    "Ja": ["Ja", "ya"],
    "Nein": ["Nein", "Nen", "Nien"],
    "n.z.": [r"n\.?\s*z\.?"],
    "Teilweise": ["Teilweise"],
    "Vollständig": ["Vollst[aä]ndig", "Vollstandig"],
    "MS Teams": ["MS Teams"],
    "Cisco WebEx": ["Cisco WebEx"],
    "Zoom": ["Zoom"],
    "Sonstiges": ["Sonstiges"],
    "Auditfrageliste": ["Auditfrageliste"],
    "Sonstige Anlagen": ["Sonstige Anlagen"],
}


def checkbox_state(line, label, window=4):
    """Best-effort: look for an uppercase 'X' within `window` chars
    immediately before an occurrence of `label` (or a known OCR-mangled
    alias of it) in `line`. Returns True/False/None (label not found)."""
    aliases = LABEL_ALIASES.get(label, [label])
    idx = None
    for alias in aliases:
        m = re.search(alias, line, re.IGNORECASE)
        if m:
            idx = m.start()
            break
    if idx is None:
        return None
    before = line[max(0, idx - window):idx]
    return "X" in before


def prefix_has_checkmark(line, max_letters=4):
    """For lines that open with a single checkbox mark followed directly by
    label text (e.g. '[X] den routinierten Umgang...'), check whether the
    leading non-lowercase-word prefix contains an uppercase 'X'."""
    stripped = line.strip()
    m = re.match(r"^([^a-zäöüß]{0,%d})" % (max_letters + 3), stripped)
    prefix = m.group(1) if m else stripped[:max_letters + 3]
    return "X" in prefix


def label_value(lines, label_pattern, flags=re.IGNORECASE, stop_pattern=None):
    """Return text following a label on the same line, scanning all lines.
    If `stop_pattern` is given (for rows that pack two label:value pairs
    onto one physical line), the captured value is truncated before it."""
    pat = re.compile(label_pattern + r"\s*[:\s]\s*(.+)$", flags)
    for ln in lines:
        m = pat.search(ln)
        if m:
            val = m.group(1)
            if stop_pattern:
                sm = re.search(stop_pattern, val, re.IGNORECASE)
                if sm:
                    val = val[:sm.start()]
            val = _strip_noise(val)
            if val:
                return val
    return None


def find_line(lines, pattern, flags=re.IGNORECASE):
    pat = re.compile(pattern, flags)
    for ln in lines:
        if pat.search(ln):
            return ln
    return None


# --------------------------------------------------------------------------
# per-document extraction
# --------------------------------------------------------------------------

class ReportExtractor:
    def __init__(self, pdf_path, debug_dir=None):
        self.pdf_path = pdf_path
        self.doc = fitz.open(pdf_path)
        self.debug_dir = debug_dir
        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
        self._page_cache = {}

    def page_lines(self, page_no, psm=4):
        """0-indexed page_no. Returns cleaned OCR text lines for the page."""
        if page_no not in self._page_cache:
            gray = render_page_gray(self.doc, page_no)
            cleaned, vmask = remove_grid_lines(gray)
            text = ocr_text(cleaned, psm=psm)
            self._page_cache[page_no] = {
                "gray": gray, "cleaned": cleaned, "vmask": vmask, "text": text,
            }
            if self.debug_dir:
                cv2.imwrite(os.path.join(self.debug_dir, f"page{page_no + 1}_cleaned.png"), cleaned)
                with open(os.path.join(self.debug_dir, f"page{page_no + 1}_ocr.txt"), "w") as f:
                    f.write(text)
        return clean_lines(self._page_cache[page_no]["text"])

    def page_column_bands(self, page_no, y0_frac=0.0, y1_frac=1.0):
        vmask = self._page_cache[page_no]["vmask"]
        return vertical_line_centers(vmask)

    def _clustered_lines(self, page_no, y_tol=15):
        """OCR words on the page grouped into visual lines by y-proximity,
        each with its full text and (y0, y1) pixel bounds. Used wherever we
        need accurate row bounding boxes (for ink-ratio checkbox reads)
        rather than just a text string."""
        self.page_lines(page_no)  # ensure page is rendered/cached
        cleaned = self._page_cache[page_no]["cleaned"]
        if "words" not in self._page_cache[page_no]:
            self._page_cache[page_no]["words"] = ocr_words(cleaned, psm=6)
        words = self._page_cache[page_no]["words"]
        words_sorted = sorted(words, key=lambda w: w["y"])
        lines = []
        for w in words_sorted:
            placed = False
            for line in lines:
                if abs(line["y0"] - w["y"]) < y_tol:
                    line["words"].append(w)
                    line["y0"] = min(line["y0"], w["y"])
                    line["y1"] = max(line["y1"], w["y"] + w["h"])
                    placed = True
                    break
            if not placed:
                lines.append({"y0": w["y"], "y1": w["y"] + w["h"], "words": [w]})
        for line in lines:
            line["words"].sort(key=lambda w: w["x"])
            line["text"] = " ".join(w["text"] for w in line["words"])
        lines.sort(key=lambda l: l["y0"])
        return lines

    # ---- page 1 ----
    def extract_stammdaten(self):
        L = self.page_lines(0)
        d = {
            "name_der_organisation": label_value(L, r"Name der Organisation"),
            "name_der_uebergeordneten_gruppe": None,
            "strasse": label_value(L, r"Stra[ßB]e"),
            "plz_ort_land": label_value(L, r"PLZ\s*/\s*Ort\s*/\s*Land"),
            "ansprechpartner": label_value(L, r"Ansprechpartner"),
            "email": label_value(L, r"^E-Mail(?!\s*Auditteamleiter)"),
            "telefon": label_value(L, r"Telefon"),
            "systemdokumentation_revision_ausgabe": label_value(L, r"Systemdokumentation"),
            "schichtbetrieb": label_value(L, r"Schichtbetrieb"),
            "sprache": label_value(L, r"Sprache"),
            "besonderheiten": label_value(L, r"Besonderheiten"),
        }
        multi_line = find_line(L, r"Auswahl der auditierten Standorte")
        aufl_line = find_line(L, r"Eine geeignete Auflistung aller Standorte")
        multi = {
            "auswahl_standorte_durch_stichprobenverfahren": self._ja_nein_nz(multi_line),
            "auflistung_standorte_bestandteil_auditdokumentation": self._ja_nz(aufl_line),
        }

        auditprofil = {
            "vertragsnummer_ze": label_value(L, r"Vertragsnummer \(ZE\)"),
            "auditzyklus": label_value(L, r"Auditzyklus"),
            "auditteamleiter": label_value(L, r"^Auditteamleiter"),
            "email_auditteamleiter": label_value(L, r"E-Mail Auditteamleiter"),
        }
        team_line = find_line(L, r"^Auditteam\b")
        team = []
        if team_line:
            rest = re.sub(r"^Auditteam\s*", "", team_line, flags=re.IGNORECASE)
            for m in re.finditer(r"([A-ZÄÖÜ][\w.\-]+(?:\s+[A-ZÄÖÜ][\w.\-]+)?)\s*\(([^)]+)\)", rest):
                team.append({"name": m.group(1).strip(), "rolle": m.group(2).strip()})
            idx = L.index(team_line)
            if idx + 1 < len(L):
                nxt = L[idx + 1]
                if "(" in nxt and not re.search(r"Fachexperte|Trainee|Beobachter", nxt, re.IGNORECASE):
                    for m in re.finditer(r"([A-ZÄÖÜ][\w.\-]+(?:\s+[A-ZÄÖÜ][\w.\-]+)?)\s*\(([^)]+)\)", nxt):
                        team.append({"name": m.group(1).strip(), "rolle": m.group(2).strip()})
        auditprofil["auditteam"] = team
        auditprofil["fachexperte_in"] = label_value(L, r"Fachexperte")
        auditprofil["trainee"] = label_value(L, r"Trainee")
        auditprofil["beobachter"] = label_value(L, r"Beobachter")

        auditprofil["standards_unter_vertrag"] = []  # filled in by build_report_json from page 2
        return d, multi, auditprofil

    def _ja_nein_nz(self, line):
        if not line:
            return None
        for opt in ["Ja", "Nein", "n.z."]:
            if checkbox_state(line, opt):
                return opt if opt != "n.z." else "n.z."
        return None

    def _ja_nz(self, line):
        if not line:
            return None
        for opt in ["Ja", "n.z."]:
            if checkbox_state(line, opt):
                return opt
        return None

    # ---- page 2 ----
    def extract_auditierte_standards(self):
        L = self.page_lines(1)
        text = "\n".join(L)
        blocks = re.split(r"(?=ISO\s*\d+\s*:\s*\d{4})", text)
        standards = []
        for block in blocks:
            m = re.search(r"(ISO\s*\d+\s*:\s*\d{4})\s+(\S.*?audit)", block, re.IGNORECASE)
            if not m:
                continue
            std_lines = clean_lines(block)
            standards.append({
                "standard": re.sub(r"\s+", " ", m.group(1)).strip(),
                "auditart": _strip_noise(m.group(2)),
                "zertifikatsnummer": label_value(std_lines, r"Zertifikatsnummer", stop_pattern=r"G[uü]ltig bis"),
                "gueltig_bis": label_value(std_lines, r"G[uü]ltig bis"),
                "geltungsbereich": self._geltungsbereich(std_lines),
                "industrie_branche": label_value(std_lines, r"Industrie\s*/\s*Branche[^)]*\)"),
                "nichtanwendbarkeit_von_kapiteln": label_value(std_lines, r"Nichtanwendbarkeit von Kapiteln"),
                "anzahl_beruecksichtigte_ma": self._to_int(label_value(std_lines, r"Anzahl ber[uü]cksichtigte MA", stop_pattern=r"Anzahl Standorte")),
                "anzahl_standorte": self._to_int(label_value(std_lines, r"Anzahl Standorte")),
                "auditleiter": label_value(std_lines, r"Auditleiter", stop_pattern=r"Auditnummer"),
                "auditnummer_za": label_value(std_lines, r"Auditnummer\s*\(?ZA\)?"),
            })
        einheit = {
            "verwendete_einheit": label_value(L, r"Verwendete Einheit"),
            "hinweis": find_line(L, r"Auditstunden"),
        }
        return standards, einheit

    def _geltungsbereich(self, lines):
        start = None
        for i, ln in enumerate(lines):
            if re.search(r"Geltungsbereich", ln, re.IGNORECASE):
                start = i
                break
        if start is None:
            return None
        parts = [re.sub(r".*?Geltungsbereich\s*:?\s*", "", lines[start], flags=re.IGNORECASE)]
        for ln in lines[start + 1:]:
            if re.search(r"Industrie\s*/\s*Branche", ln, re.IGNORECASE):
                break
            parts.append(ln)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    @staticmethod
    def _to_int(s):
        if not s:
            return None
        m = re.search(r"\d+", s)
        return int(m.group()) if m else None

    @staticmethod
    def _to_float(s):
        if not s:
            return None
        m = re.search(r"\d+[.,]\d+|\d+", s)
        return float(m.group().replace(",", ".")) if m else None

    # ---- page 3 ----
    def extract_audit_details_remote(self):
        L = self.page_lines(2)
        standorte_line = find_line(L, r"^Standorte\b")
        standorte = []
        if standorte_line:
            idx = L.index(standorte_line)
            block = [re.sub(r"^Standorte\s*", "", standorte_line)]
            for ln in L[idx + 1:]:
                if re.search(r"Audit-Datum", ln, re.IGNORECASE):
                    break
                block.append(ln)
            joined = " ".join(block)
            standorte = [s.strip() for s in re.split(r",\s*", joined) if s.strip()]

        datum_line = find_line(L, r"Audit-Datum")
        von, bis = None, None
        if datum_line:
            m = re.search(r"(\d{4}-\d{2}-\d{2}).*?(\d{4}-\d{2}-\d{2})", datum_line)
            if m:
                von, bis = m.group(1), m.group(2)

        umfang_line = find_line(L, r"Audit-Umfang")
        umfang = self._to_float(umfang_line) if umfang_line else None
        davon_line = find_line(L, r"Stufe 1-Audit")
        davon = self._to_float(davon_line) if davon_line else None

        audit_details = {
            "standorte": standorte,
            "audit_datum_von": von,
            "audit_datum_bis": bis,
            "audit_umfang_personentage_vor_ort": umfang,
            "davon_personentage_stufe1_audit": davon,
            "stufe1_gesondert_berichtet": bool(davon_line and "gesondert berichtet" in davon_line.lower()),
        }

        remote_line = find_line(L, r"Auditdurchf[uü]hrung als Remote-Audit")
        remote_mode = None
        if remote_line:
            for opt in ["Vollständig", "Teilweise", "Nein"]:
                if checkbox_state(remote_line, opt):
                    remote_mode = opt
                    break

        technik_line = find_line(L, r"MS Teams")
        techniken = {
            "ms_teams": bool(technik_line and checkbox_state(technik_line, "MS Teams")),
            "cisco_webex": bool(technik_line and checkbox_state(technik_line, "Cisco WebEx")),
            "zoom": bool(technik_line and checkbox_state(technik_line, "Zoom")),
            "sonstiges_auf_kundenwunsch": bool(find_line(L, r"Sonstiges auf Wunsch") and
                                                checkbox_state(find_line(L, r"Sonstiges auf Wunsch"), "Sonstiges")),
        }

        pct_line = find_line(L, r"wurde zu \d+% mit Hilfe")
        remote_pct = None
        if pct_line:
            m = re.search(r"(\d+)\s*%", pct_line)
            remote_pct = int(m.group(1)) if m else None

        eff_items = {
            "routinierter_umgang_mit_technologie": "den routinierten Umgang",
            "verzoegerungsfreier_ablauf_der_sitzungen": "verz[oö]gerungsfreien Ablauf",
            "online_befragung_verschiedener_personen": "Online-Befragung",
            "trennung_auditteam_separate_online_sitzungen": "Trennung des Auditteams",
            "einsicht_in_stichproben_dokumentierter_prozesse": "Einsicht in angemessene Stichproben",
            "diskussion_schaubilder_diagramme_praesentationen": "Diskussion von geeigneten Schaubildern",
            "praesentation_diskussion_fotos_videos_tonaufnahmen": "Pr[aä]sentation und Diskussion von Fotos",
        }
        effektivitaet = {}
        for key, pat in eff_items.items():
            ln = find_line(L, pat)
            effektivitaet[key] = bool(ln and prefix_has_checkmark(ln))

        remote_audit = {
            "auditdurchfuehrung_als_remote_audit": remote_mode,
            "genutzte_techniken": techniken,
            "remote_anteil_prozent": remote_pct,
            "effektivitaet_sichergestellt_durch": effektivitaet,
        }
        return audit_details, remote_audit

    # ---- page 4 ----
    def extract_anlagen(self):
        L = self.page_lines(3)
        ln = find_line(L, r"Auditfrageliste")
        anlagen = {
            "auditfrageliste_checkliste_fragebogen": bool(ln and checkbox_state(ln, "Auditfrageliste")),
        }
        ln2 = find_line(L, r"Sonstige Anlagen")
        anlagen["sonstige_anlagen"] = bool(ln2 and checkbox_state(ln2, "Sonstige Anlagen"))
        return anlagen

    # ---- page 5 ----
    def extract_auditergebnis(self):
        L = self.page_lines(4)
        start = None
        end = None
        for i, ln in enumerate(L):
            if re.search(r"^Kapitel\s+Erg", ln):
                start = i + 1
            if re.search(r"Erg[aä]nzungen zum Klimawandel", ln):
                end = i
                break
        rows = L[start:end] if start is not None and end is not None else []

        # Each row is "<kapitel> <erg> <kapitel> <erg>" but OCR mangles the
        # "X.Y" kapitel numbers unpredictably (dropped dot, stray digits/
        # letters glued on). We don't trust the kapitel token at all — chapter
        # identity comes from the published standard's fixed clause list
        # (ISO_9001_2015_CHAPTERS / ISO_14001_2015_CHAPTERS) matched by row
        # order — but we do need the *pairing* of "number, then a lone 0-3/-
        # digit" to reliably pull out just the Erg value and skip over
        # digits that are actually part of the kapitel number itself (e.g.
        # not misreading the "1" in "4.1" as an Erg score).
        tokens_per_row = []
        for ln in rows:
            pairs = re.findall(r"\d{1,2}(?:[.:]\d{1,2})?[\s|,ı!]+([0-3-])(?!\d)", ln)
            tokens_per_row.append(pairs)

        iso9001 = self._assign_chapter_scores(tokens_per_row, col_index=0, n_cols_hint=2)
        iso14001 = self._assign_chapter_scores(tokens_per_row, col_index=1, n_cols_hint=2)

        klimawandel_line = find_line(L, r"Nachweisbare Ber[uü]cksichtigung des Klimawandels")
        klimawandel = self._to_int(klimawandel_line.split()[-1]) if klimawandel_line else None

        req_map = {
            "interne_audits_und_managementbewertung": r"Interne Audits und Managementbewertung",
            "bewertung_massnahmen_nichtkonformitaeten_letztes_audit": r"Bewertung von Ma[ßs]nahmen zu Nichtkonformit[aä]ten",
            "umgang_mit_beschwerden": r"Umgang mit Beschwerden",
            "wirksamkeit_managementsystem_erreichen_der_ziele": r"Wirksamkeit des Managementsystems",
            "fortschritt_geplante_taetigkeiten_staendige_verbesserung": r"Fortschritt bei geplanten T[aä]tigkeiten",
            "leistungsfaehigkeit_managementsystem_bindende_verpflichtungen": r"Leistungsf[aä]higkeit des Managementsystems",
            "operative_lenkung_prozesse_des_kunden": r"Operative Lenkung der Prozesse",
            "bewertung_aenderungen_inkl_managementdokumentation": r"Bewertung von [AÄ]nderungen",
            "nutzung_zeichen_verweise_auf_zertifizierung": r"Nutzung von Zeichen",
        }
        zusatz = {}
        for key, pat in req_map.items():
            ln = find_line(L, pat)
            if ln:
                m = re.search(r"([0-3-])\s*$", ln.strip())
                zusatz[key] = m.group(1) if m else None
                if zusatz[key] and zusatz[key] != "-":
                    zusatz[key] = int(zusatz[key])
            else:
                zusatz[key] = None

        return {
            "erg_legende": {
                "0": "nicht auditiert",
                "1": "erfüllt",
                "2": "grundsätzlich erfüllt / Verbesserungspotenzial",
                "3": "nicht erfüllt / Nichtkonformität",
                "-": "nicht zutreffend / ausgeschlossen",
            },
            "iso_9001_2015": iso9001,
            "iso_14001_2015": iso14001,
            "ergaenzungen_klimawandel": {"nachweisbare_beruecksichtigung_klimawandel_4_1_4_2": klimawandel},
            "zusaetzliche_anforderungen_iso_17021_2015": zusatz,
        }

    def _assign_chapter_scores(self, tokens_per_row, col_index, n_cols_hint):
        chapters = ISO_9001_2015_CHAPTERS if col_index == 0 else ISO_14001_2015_CHAPTERS
        scores = {}
        for i, ch in enumerate(chapters):
            if i < len(tokens_per_row):
                row_tokens = tokens_per_row[i]
                if len(row_tokens) > col_index:
                    val = row_tokens[col_index]
                    scores[ch] = val if val == "-" else int(val)
                else:
                    scores[ch] = None
            else:
                scores[ch] = None
        return scores

    # ---- page 6 ----
    def extract_pflichtelemente(self):
        L = self.page_lines(5)
        temp_line = find_line(L, r"Sind tempor[aä]re Standorte")
        nein_line = find_line(L, r"Nein\b")
        vorhanden = None
        if temp_line and checkbox_state(temp_line, "Ja"):
            vorhanden = True
        elif nein_line and checkbox_state(nein_line, "Nein"):
            vorhanden = False

        beschreibung = None
        idx_b = next((i for i, ln in enumerate(L) if re.search(r"Falls ja, welche wurden audi", ln, re.IGNORECASE)), None)
        if idx_b is not None:
            parts = [re.sub(r".*?audi\w*\??\s*", "", L[idx_b], flags=re.IGNORECASE)]
            for ln in L[idx_b + 1:]:
                if re.search(r"Objektive Nachweise", ln, re.IGNORECASE):
                    break
                parts.append(ln)
            beschreibung = re.sub(r"\s+", " ", " ".join(parts)).strip()
            # the "Nein" checkbox for this same question sits far enough to
            # the right that OCR's reading order tacks a stray "DnNein"-like
            # fragment onto the end of this paragraph; strip it back off.
            beschreibung = re.sub(r"\s*[A-Z]?[Nn]ein\.?\s*$", "", beschreibung).strip() or None

        nachweise_labels = [
            "Auszug eines Berufs- oder Handelsregisters",
            "Organigramm/Dokumentation der Aufbauorganisation",
            "Unternehmenspolitik zu den auditierten Managementsystemen",
            "Übersicht zur Dokumentation des Managementsystems",
            "Ergebnis der Bewertung des Managementsystems",
            "Auditjahresplanung für interne Audits",
            "Nachweise über durchgeführte interne Audits",
            "ISO 14001: Auszug aus dem Genehmigungskataster",
            "ISO 27001: Erklärung zur Anwendbarkeit",
            "ISO 45001: Unfallstatistik",
            "ISO 50001: Inhaltsverzeichnis des Energieberichtes",
            "Sonstiges",
        ]

        # "Beigefügt" is a lone checkbox glyph per row with no adjacent text
        # to anchor a text heuristic to, so it's read by pixel ink-density
        # instead: crop each row's cell in the Beigefügt column and compare
        # against the column's own baseline (checked rows stand out as a
        # clear outlier above the median of all rows).
        gray = self._page_cache[5]["gray"]
        vmask = self._page_cache[5]["vmask"]
        centers = vertical_line_centers(vmask, min_height_frac=0.03)
        beigefuegt_band = (centers[-2], centers[-1]) if len(centers) >= 2 else None
        row_lines = self._clustered_lines(5)

        row_ink = {}
        for label in nachweise_labels:
            key_words = label.split(":")[0][:25]
            row = next((r for r in row_lines
                        if key_words.lower()[:15] in r["text"].lower()), None)
            row_ink[label] = (row, ink_ratio(gray, beigefuegt_band[0], row["y0"] - 5, beigefuegt_band[1], row["y1"] + 5)
                               if row and beigefuegt_band else 0.0)

        ratios = [v for _, v in row_ink.values() if v]
        baseline = sorted(ratios)[len(ratios) // 2] if ratios else 0.0  # median

        objektive_nachweise = []
        for label in nachweise_labels:
            row, ratio = row_ink[label]
            ausgabe = None
            if row:
                # search this row's text plus the next couple of physical
                # OCR lines, since the "Ausgabe" date frequently wraps onto
                # a continuation line of the table cell
                li = next((i for i, ln in enumerate(L) if label.split(":")[0][:25].lower()[:15] in ln.lower()), None)
                window = " ".join(L[li:li + 3]) if li is not None else row["text"]
                date_m = re.search(r"(z\.\s?B\.?\s*\w+\s*-?\s*[\d.]{6,10}|akt\.\s*[\d.]{6,10}|[\d]{2}\.[\d]{2}\.[\d]{4})", window)
                ausgabe = date_m.group(1).strip() if date_m else None
            objektive_nachweise.append({
                "bezeichnung_inhalt": label,
                "ausgabe": ausgabe,
                "beigefuegt": bool(ratio > baseline + 0.02),
            })

        std_erg_line = find_line(L, r"Weitere standardspezifische Auditergebnisse")
        return {
            "temporaere_standorte": {
                "vorhanden": vorhanden,
                "auditierte_standorte_beschreibung": beschreibung,
            },
            "objektive_nachweise": objektive_nachweise,
            "standardspezifische_ergebnisse": {
                "weitere_ergebnisse_in_ergaenzenden_auditberichten": bool(
                    std_erg_line and re.search(r"^\s*[xX]", std_erg_line.strip())
                ),
            },
        }

    # ---- page 7-9: Firmenprofil / Zusammenfassung ----
    def extract_firmenprofil_zusammenfassung(self):
        L7 = self.page_lines(6)
        L8 = self.page_lines(7)
        L9 = self.page_lines(8)

        firmenprofil = {
            "produktpalette": label_value(L7, r"Produktpalette"),
            "kunden_hauptkunden": self._multi_line_value(L7, r"Kunden\s*/\s*Hauptkunden", r"Wesentliche Prozesse"),
            "wesentliche_prozesse": self._multi_line_value(L7, r"Wesentliche Prozesse", r"Wichtige umweltrelevante|Zulassungen"),
            "umweltrelevante_taetigkeiten": self._multi_line_value(L7, r"Wichtige umweltrelevante T[aä]tigkeiten", r"Zulassungen/Zertifikate"),
            "zulassungen_zertifikate": self._multi_line_value(L7, r"Zulassungen/Zertifikate", r"QMS Zertifiziert seit"),
            "qms_zertifiziert_seit": self._to_int(label_value(L7, r"QMS Zertifiziert seit")),
            "ums_zertifiziert_seit": self._to_int(label_value(L7, r"UMS Zertifiziert seit")),
        }

        narrativ = self._multi_line_value(L7, r"^Zusammenfassung$", r"^[QU/]{1,3}\s*1\.")

        tochterunternehmen = []
        full_text = "\n".join(L7)
        item_pattern = re.compile(
            r"(?P<kuerzel_qu>Q/U|Q|U)\s*(?P<nr>\d+)\.\s*(?P<rest>.+?)(?=(?:\n(?:Q/U|Q|U)\s*\d+\.)|\nAuditiert wurde|\Z)",
            re.DOTALL,
        )
        for m in item_pattern.finditer(full_text):
            rest = re.sub(r"\s+", " ", m.group("rest")).strip()
            parts = [p.strip() for p in rest.split(",")]
            kuerzel = None
            tail_m = re.search(r"[-–—]\s*([A-ZÄÖÜ]{2,6})\s*$", rest)
            if tail_m:
                kuerzel = tail_m.group(1)
                rest_wo_kuerzel = rest[:tail_m.start()].strip(" -–—")
                parts = [p.strip() for p in rest_wo_kuerzel.split(",")]
            name = parts[0] if len(parts) > 0 else None
            standort = parts[1] if len(parts) > 1 else None
            schwerpunkt = ", ".join(parts[2:]) if len(parts) > 2 else None
            tochterunternehmen.append({
                "nr": int(m.group("nr")),
                "geltungsbereich_kuerzel": m.group("kuerzel_qu").strip(),
                "name": name,
                "standort": standort,
                "schwerpunkt": schwerpunkt,
                "kuerzel": kuerzel,
            })

        audit_nummern_line = find_line(L7, r"Auditiert wurde die Zentrale")
        nummern = []
        if audit_nummern_line:
            nummern = [int(n) for n in re.findall(r"\d+", audit_nummern_line)]

        erweiterung_m = re.search(r"(Der Geltungsbereich f[uü]r die.*?erweitert\.?)",
                                   "\n".join(L7), re.DOTALL | re.IGNORECASE)
        erweiterung_line = re.sub(r"\s+", " ", erweiterung_m.group(1)).strip() if erweiterung_m else None

        qm_erg = self._multi_line_value(L8, r"Ergebnisse in Bezug auf das QM-System", r"Ergebnisse in Bezug auf das UM-System")
        um_erg_p8 = self._multi_line_value(L8, r"Ergebnisse in Bezug auf das UM-System", r"\Z")
        um_erg_p9 = "\n".join(L9)
        um_erg = (um_erg_p8 or "") + "\n" + um_erg_p9

        schwerpunkt_line = self._multi_line_value(L8, r"Ein Schwerpunkt im n[aä]chsten Audit", r"Ergebnisse in Bezug auf das QM-System")

        zusammenfassung = {
            "narrativ": narrativ,
            "tochterunternehmen": tochterunternehmen,
            "auditierte_standorte_nummern": nummern,
            "geltungsbereich_erweiterung_2025": erweiterung_line,
            "qm_system_ergebnisse": qm_erg,
            "um_system_ergebnisse": um_erg.strip() if um_erg else None,
            "naechster_audit_schwerpunkt": schwerpunkt_line,
        }
        return firmenprofil, zusammenfassung

    def _multi_line_value(self, lines, start_pattern, end_pattern):
        start_re = re.compile(start_pattern, re.IGNORECASE)
        end_re = re.compile(end_pattern, re.IGNORECASE)
        start_idx = None
        for i, ln in enumerate(lines):
            if start_re.search(ln):
                start_idx = i
                break
        if start_idx is None:
            return None
        first = start_re.sub("", lines[start_idx]).strip(" :")
        collected = [first] if first else []
        for ln in lines[start_idx + 1:]:
            if end_re.search(ln):
                break
            collected.append(ln)
        return re.sub(r"\s+", " ", " ".join(collected)).strip() or None

    # ---- page 10: Schlussfolgerungen ----
    def extract_schlussfolgerungen(self):
        L = self.page_lines(9)
        narrativ = self._multi_line_value(L, r"^Unter Ber[uü]cksichtigung", r"Dies umfasst insbesondere")
        return {"narrativ": narrativ}

    # ---- page 11: Nichtkonformitäten ----
    def extract_nichtkonformitaeten(self):
        L = self.page_lines(10)
        row_labels = ["ISO 9001 : 2015", "ISO 14001 : 2015", "---:---", "---:---"]
        table = []
        text = "\n".join(L)
        for pat, label in [(r"ISO\s*9001\s*:\s*2015", "ISO 9001:2015"), (r"ISO\s*14001\s*:\s*2015", "ISO 14001:2015")]:
            ln = find_line(L, pat)
            nums = re.findall(r"\d+", ln) if ln else []
            nums = [n for n in nums if n not in ("9001", "14001", "2015")]
            a, b, c = (nums + [None, None, None])[:3]
            table.append({
                "standard": label,
                "anzahl_nc_a_erstellt_in_diesem_audit": int(a) if a is not None else None,
                "anzahl_nc_b_erstellt_in_diesem_audit": int(b) if b is not None else None,
                "anzahl_nc_zu_verifizieren_aus_vorigem_audit": int(c) if c is not None else None,
            })
        summe_line = find_line(L, r"^Summe\b")
        summe_nums = [int(n) for n in re.findall(r"\d+", summe_line)] if summe_line else []
        gesamt_line = find_line(L, r"Gesamtzahl der in diesem Audit erstellten")
        gesamt = self._to_int(gesamt_line.split(":")[-1]) if gesamt_line and ":" in gesamt_line else None

        allgemein_line = find_line(L, r"allgemein g[uü]ltig")
        verifiziert_line = find_line(L, r"Umsetzung und die Wirksamkeit von Korrekturma[ßs]nahmen")

        return {
            "zusammenfassung_tabelle": table,
            "summe": {
                "anzahl_nc_a": summe_nums[0] if len(summe_nums) > 0 else None,
                "anzahl_nc_b": summe_nums[1] if len(summe_nums) > 1 else None,
                "anzahl_nc_zu_verifizieren": summe_nums[2] if len(summe_nums) > 2 else None,
            },
            "gesamtzahl_erstellte_nichtkonformitaetsberichte": gesamt,
            "mindestens_eine_nc_allgemein_gueltig": bool(allgemein_line and prefix_has_checkmark(allgemein_line)),
            "umsetzung_wirksamkeit_korrekturmassnahmen_vorheriges_audit_bewertet": bool(
                verifiziert_line and prefix_has_checkmark(verifiziert_line)),
        }

    # ---- pages 12-14: numbered multi-column tables (VP / PA / Anm) ----
    # Each of these tables can run onto a second page without repeating its
    # header row (VP spills from page 12 onto page 13's top; PA spills from
    # 13 onto 14). So this scans a *list* of candidate pages, builds one
    # continuous "Nr." sequence across all of them, and only accepts a row
    # number that is exactly one more than the last accepted number — that
    # naturally absorbs true continuation rows and rejects a different
    # table's numbering restarting at 1 on a later page.
    def extract_numbered_table(self, page_nos, header_pattern, stop_pattern=None):
        full_text = "\n".join(ln for p in page_nos for ln in self.page_lines(p))
        if not re.search(header_pattern, full_text, re.IGNORECASE):
            return []

        per_page = {}
        for page_no in page_nos:
            gray = render_page_gray(self.doc, page_no)
            cleaned, vmask = remove_grid_lines(gray)
            centers = vertical_line_centers(vmask)
            if len(centers) < 4:
                continue
            col_bounds = list(zip(centers[:-1], centers[1:]))
            col_words = []
            for (x0, x1) in col_bounds:
                band = cleaned[:, x0:x1]
                words = ocr_words(band, psm=6)
                for w in words:
                    w["x"] += x0
                col_words.append(words)
            per_page[page_no] = {"col_words": col_words, "height": cleaned.shape[0]}

        row_anchors = []  # (page_no, nr, y)
        for page_no in page_nos:
            if page_no not in per_page:
                continue
            for w in per_page[page_no]["col_words"][0]:
                # Tesseract glues assorted punctuation onto the row number
                # depending on font rendering ("1.", "5;", "12%", "11:"), so
                # strip any non-digit chars rather than just ".," specifically.
                t = re.sub(r"^\D+|\D+$", "", w["text"].strip())
                if re.fullmatch(r"\d{1,2}", t):
                    row_anchors.append((page_no, int(t), w["y"]))
        row_anchors.sort(key=lambda r: (page_nos.index(r[0]), r[2]))

        # Require strictly increasing numbers with only a small forward gap
        # allowed, rather than an exact "== last+1" match. An exact-match
        # rule is fragile against a single misread digit (e.g. "13" OCR'd
        # as "19"): that one row's number is simply wrong, not missing, so
        # it never equals expected_next and everything after it would be
        # rejected forever. Tolerating a gap lets the *next* correctly-read
        # row (14, 15, 16, ...) resynchronize instead of being lost along
        # with the one bad row.
        MAX_GAP = 5
        dedup = []
        last_nr = 0
        for page_no, nr, y in row_anchors:
            if last_nr < nr <= last_nr + MAX_GAP:
                dedup.append((page_no, nr, y))
                last_nr = nr
        row_anchors = dedup
        if not row_anchors:
            return []

        # The wider columns' text is vertically centered on the row and so
        # routinely starts ~20-25px *above* the "Nr." digit's own y (a
        # single small glyph sits lower in its line-box than a full text
        # line does). An 8px margin left the previous/next row's first line
        # bleeding across the boundary; empirically ~25px clears it.
        ROW_MARGIN = 25
        bands = []
        for i, (page_no, nr, y) in enumerate(row_anchors):
            y_top = max(0, y - ROW_MARGIN)
            if i + 1 < len(row_anchors) and row_anchors[i + 1][0] == page_no:
                y_bot = row_anchors[i + 1][2] - ROW_MARGIN
            else:
                # Last accepted row: if the *next* table's header sits on
                # this same page (e.g. VP's item 20 and PA's header both on
                # page 13), stop before it instead of running to the
                # physical bottom of the page and swallowing that table too.
                y_bot = per_page[page_no]["height"]
                if stop_pattern:
                    stop_line = next((l for l in self._clustered_lines(page_no)
                                       if l["y0"] > y_top and re.search(stop_pattern, l["text"], re.IGNORECASE)),
                                      None)
                    if stop_line:
                        y_bot = max(y_top + 1, stop_line["y0"] - ROW_MARGIN)
            bands.append((page_no, nr, y_top, y_bot))

        col_field_names = ["beschreibung", "bereich_prozess", "norm_forderung"]
        results = []
        for page_no, nr, y_top, y_bot in bands:
            row = {"nr": nr}
            col_words = per_page[page_no]["col_words"]
            for ci, field in enumerate(col_field_names, start=1):
                words = [w for w in col_words[ci] if y_top <= w["y"] < y_bot]
                words.sort(key=lambda w: (w["y"] // 15, w["x"]))
                row[field] = re.sub(r"\s+", " ", " ".join(w["text"] for w in words)).strip()
            results.append(row)
        return results

    # ---- page 15 ----
    def extract_abschluss(self):
        L = self.page_lines(14)

        # This matrix has no per-cell text label to anchor a text heuristic
        # to (every cell is just a bare checkbox glyph), and the marks are
        # small enough that OCR word-detection drops several of them
        # entirely. Read it by pixel ink-density instead: find each row's
        # y-band from its label text, each column's x-band from the
        # detected vertical rule lines, then flag a cell as checked when its
        # ink ratio stands out above that row's own minimum (checked cells
        # are a clear local outlier in every row we've observed).
        gray = self._page_cache[14]["gray"]
        vmask = self._page_cache[14]["vmask"]
        centers = vertical_line_centers(vmask, min_height_frac=0.05)
        col_bounds = list(zip(centers[1:-1], centers[2:])) if len(centers) >= 3 else []
        lines = self._clustered_lines(14)

        row_patterns = {
            "erfuellt": r"^Erf[uü]llt",
            "offen": r"Offen:?\s*Nichtkonformit",
            "nicht_erfuellt": r"^Nicht erf[uü]llt",
            "erteilung": r"Erteilung.*Erweiterung.*Erneuerung",
            "aufrechterhaltung": r"^Aufrechterhaltung",
            "aussetzung": r"^Aussetzung",
            "wiederherstellung": r"^Wiederherstellung",
            "verweigerung": r"^Verweigerung",
            "zurueckziehung": r"Zur[uü]ckziehung",
        }

        def row_checks(pattern, margin=0.015):
            line = next((l for l in lines if re.search(pattern, l["text"], re.IGNORECASE)), None)
            if not line or not col_bounds:
                return None, None
            ratios = [ink_ratio(gray, x0, line["y0"] - 5, x1, line["y1"] - 5) for x0, x1 in col_bounds]
            base = min(ratios)
            checked = [r - base > margin for r in ratios]
            checked += [False] * (4 - len(checked))
            return checked[0], checked[1]

        results = {k: row_checks(p) for k, p in row_patterns.items()}
        erfuellt_9001, erfuellt_14001 = results["erfuellt"]
        offen_9001, offen_14001 = results["offen"]
        nicht_erfuellt_9001, nicht_erfuellt_14001 = results["nicht_erfuellt"]
        erteilung_9001, erteilung_14001 = results["erteilung"]
        aufrecht_9001, aufrecht_14001 = results["aufrechterhaltung"]
        aussetzung_9001, aussetzung_14001 = results["aussetzung"]
        wiederherst_9001, wiederherst_14001 = results["wiederherstellung"]
        verweigerung_9001, verweigerung_14001 = results["verweigerung"]
        zurueck_9001, zurueck_14001 = results["zurueckziehung"]

        naechster_hinweis = self._multi_line_value(L, r"^Beim n[aä]chsten Audit wird", r"F[uü]r das n[aä]chste Audit")
        zeitraum_line = find_line(L, r"F[uü]r das n[aä]chste Audit ist vorl[aä]ufig vereinbart")
        zeitraum = None
        if zeitraum_line:
            m = re.search(r"([\d.\-]+)\s*$", zeitraum_line)
            zeitraum = m.group(1) if m else None

        name = label_value(L, r"^Name")
        datum_line = find_line(L, r"Datum\s*:")
        datum = None
        if datum_line:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", datum_line)
            datum = m.group(1) if m else None

        return {
            "abschluss_und_empfehlungen": {
                "abschliessendes_ergebnis": {
                    "iso_9001": {"erfuellt": erfuellt_9001, "offen_nichtkonformitaeten": offen_9001, "nicht_erfuellt": nicht_erfuellt_9001},
                    "iso_14001": {"erfuellt": erfuellt_14001, "offen_nichtkonformitaeten": offen_14001, "nicht_erfuellt": nicht_erfuellt_14001},
                },
                "empfehlung_des_auditteams": {
                    "iso_9001": {
                        "erteilung_erweiterung_erneuerung": erteilung_9001,
                        "aufrechterhaltung": aufrecht_9001,
                        "aussetzung": aussetzung_9001,
                        "wiederherstellung": wiederherst_9001,
                        "verweigerung": verweigerung_9001,
                        "zurueckziehung": zurueck_9001,
                    },
                    "iso_14001": {
                        "erteilung_erweiterung_erneuerung": erteilung_14001,
                        "aufrechterhaltung": aufrecht_14001,
                        "aussetzung": aussetzung_14001,
                        "wiederherstellung": wiederherst_14001,
                        "verweigerung": verweigerung_14001,
                        "zurueckziehung": zurueck_14001,
                    },
                },
            },
            "anmerkungen_zum_naechsten_audit": {
                "hinweis": naechster_hinweis,
                "naechster_audit_zeitraum_vorlaeufig": zeitraum,
            },
            "verantwortlich_fuer_den_inhalt": {
                "name": name,
                "datum": datum,
                "unterschrift_vorhanden": True,
            },
        }

    # ---- header (all pages) ----
    def extract_document_header(self):
        L = self.page_lines(0)
        title_line = find_line(L, r"Auditbericht \(Stufe")
        org_line = find_line(L, r"^Organisation\b")
        za_line = find_line(L, r"Audits\(ZA\)")
        return {
            "title": title_line.strip() if title_line else None,
            "organisation": re.sub(r"^Organisation\s*", "", org_line).strip() if org_line else None,
            "audits_za": re.sub(r"^Audits\(ZA\)\s*", "", za_line).strip() if za_line else None,
        }


def build_report_json(pdf_path, debug_dir=None):
    ex = ReportExtractor(pdf_path, debug_dir=debug_dir)

    stammdaten, multi_standort, auditprofil = ex.extract_stammdaten()
    auditierte_standards, einheit = ex.extract_auditierte_standards()
    auditprofil["standards_unter_vertrag"] = [
        {"standard": s["standard"], "auditart": s["auditart"], "umstellungsaudit": False}
        for s in auditierte_standards
    ]
    audit_details, remote_audit = ex.extract_audit_details_remote()
    anlagen = ex.extract_anlagen()
    auditergebnis = ex.extract_auditergebnis()
    pflichtelemente = ex.extract_pflichtelemente()
    firmenprofil, zusammenfassung = ex.extract_firmenprofil_zusammenfassung()
    schlussfolgerungen = ex.extract_schlussfolgerungen()
    nichtkonformitaeten = ex.extract_nichtkonformitaeten()
    vp = ex.extract_numbered_table([11, 12], r"VP \(Verbesserungspotenzial\)", stop_pattern=r"PA \(Positive Aspekte")
    pa = ex.extract_numbered_table([12, 13], r"PA \(Positive Aspekte", stop_pattern=r"Anm \(Anmerkung\)")
    anm = ex.extract_numbered_table([13, 14], r"Anm \(Anmerkung\)", stop_pattern=r"Abschluss und Empfehlungen")
    abschluss = ex.extract_abschluss()
    header = ex.extract_document_header()

    doc_meta = {
        "source_file": os.path.basename(pdf_path),
        "source_path": os.path.abspath(pdf_path),
        "document_type": "TUEV_NORD_Auditbericht_Stufe2",
        "template_form": "A00F207",
        "page_count": ex.doc.page_count,
        "extracted_date": datetime.date.today().isoformat(),
        "confidentiality": "confidential",
        "language": "de",
        "extraction_method": "local_ocr",
        "ocr_engine": "tesseract",
        "ocr_lang": LANG,
        "ocr_dpi": DPI,
        "note": "Generated by local OCR (no text layer in source PDF). Spot-check checkbox "
                "fields and narrative paragraphs before relying on this output.",
    }

    result = {
        "extraction_meta": doc_meta,
        "document_header": header,
        "stammdaten_der_organisation": stammdaten,
        "multi_standort_organisation": multi_standort,
        "auditprofil": auditprofil,
        "auditierte_standards": auditierte_standards,
        "einheit_dauern_zeiten": einheit,
        "audit_details": audit_details,
        "remote_audit": remote_audit,
        "anlagen_ergaenzungen": anlagen,
        "auditergebnis": auditergebnis,
        "pflichtelemente_a00va02": pflichtelemente,
        "firmenprofil": firmenprofil,
        "zusammenfassung": zusammenfassung,
        "schlussfolgerungen": schlussfolgerungen,
        "nichtkonformitaeten": nichtkonformitaeten,
        "verbesserungspotenziale": vp,
        "positive_aspekte": pa,
        "anmerkungen": anm,
    }
    result.update(abschluss)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf_path")
    parser.add_argument("output_json", nargs="?", default=None)
    parser.add_argument("--debug", action="store_true", help="dump cleaned page images + raw OCR text alongside the output")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf_path):
        print(f"File not found: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)

    out_path = args.output_json or (os.path.splitext(args.pdf_path)[0] + ".json")
    debug_dir = (os.path.splitext(out_path)[0] + "_debug") if args.debug else None

    print(f"Reading {args.pdf_path} ...")
    result = build_report_json(args.pdf_path, debug_dir=debug_dir)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Wrote {out_path}")
    if debug_dir:
        print(f"Debug artifacts in {debug_dir}/")


if __name__ == "__main__":
    main()
