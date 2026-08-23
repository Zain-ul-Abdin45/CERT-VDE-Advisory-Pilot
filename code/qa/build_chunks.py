"""
Structure-aware chunking for Slice B (grounded answering + abstention).

Two paths, matching the ingestion split already established in code/matching/
(no PDF advisories exist in the corpus — see FAILURE_LOG.md #1):

- CSAF path: walk document.notes and vulnerabilities[].notes/remediations directly.
  Each chunk keeps its section type and, where applicable, its CVE ID as metadata,
  rather than flattening the whole advisory into one blob of prose.
- HTML path: split on <h2>/<h3> headings (CERT@VDE's advisory pages follow a
  consistent Summary / Impact / Affected Product(s) / Vulnerabilities / Mitigation /
  Remediation structure), one chunk per section.

Output: code/qa/chunks.json, a flat list of
  {id, advisory_id, tracking_id, format, section, cve, text}
"""
import json
from pathlib import Path

from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[2]
ADVISORIES_DIR = REPO / "data" / "advisories"
OUT_PATH = Path(__file__).resolve().parent / "chunks.json"

MIN_CHUNK_CHARS = 20  # skip near-empty notes (e.g. bare category markers)


def chunk_csaf(advisory_id: str, doc: dict) -> list[dict]:
    chunks = []
    tracking_id = doc.get("document", {}).get("tracking", {}).get("id", advisory_id)
    title = doc.get("document", {}).get("title", "")

    # Document-level notes: summary, general recommendation, legal disclaimer,
    # and the description-category notes (Remediation/Impact/Mitigation prose).
    for i, note in enumerate(doc.get("document", {}).get("notes", [])):
        text = (note.get("text") or "").strip()
        if len(text) < MIN_CHUNK_CHARS:
            continue
        chunks.append({
            "id": f"{advisory_id}::csaf::doc_note::{i}",
            "advisory_id": advisory_id,
            "tracking_id": tracking_id,
            "title": title,
            "format": "csaf",
            "section": f"document_note:{note.get('category', 'unknown')}",
            "section_title": note.get("title", ""),
            "cve": None,
            "text": text,
        })

    # Per-vulnerability notes (CVE description), CVSS scores, and remediations.
    for v in doc.get("vulnerabilities", []):
        cve = v.get("cve")
        for i, score in enumerate(v.get("scores", [])):
            cvss = score.get("cvss_v3") or score.get("cvss_v2") or {}
            base = cvss.get("baseScore")
            severity = cvss.get("baseSeverity")
            vector = cvss.get("vectorString")
            if base is None:
                continue
            text = (
                f"{cve} has a CVSS base score of {base} "
                f"(severity: {severity}, vector: {vector})."
            )
            chunks.append({
                "id": f"{advisory_id}::csaf::score::{cve}::{i}",
                "advisory_id": advisory_id,
                "tracking_id": tracking_id,
                "title": title,
                "format": "csaf",
                "section": "vulnerability_score:cvss",
                "section_title": "",
                "cve": cve,
                "text": text,
            })
        for i, note in enumerate(v.get("notes", [])):
            text = (note.get("text") or "").strip()
            if len(text) < MIN_CHUNK_CHARS:
                continue
            chunks.append({
                "id": f"{advisory_id}::csaf::vuln_note::{cve}::{i}",
                "advisory_id": advisory_id,
                "tracking_id": tracking_id,
                "title": title,
                "format": "csaf",
                "section": f"vulnerability_note:{note.get('category', 'unknown')}",
                "section_title": note.get("title", ""),
                "cve": cve,
                "text": text,
            })
        for i, rem in enumerate(v.get("remediations", [])):
            text = (rem.get("details") or "").strip()
            if len(text) < MIN_CHUNK_CHARS:
                continue
            chunks.append({
                "id": f"{advisory_id}::csaf::remediation::{cve}::{i}",
                "advisory_id": advisory_id,
                "tracking_id": tracking_id,
                "title": title,
                "format": "csaf",
                "section": f"remediation:{rem.get('category', 'unknown')}",
                "section_title": "",
                "cve": cve,
                "text": text,
            })
    return chunks


def chunk_html(advisory_id: str, html_path: Path) -> list[dict]:
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    chunks = []

    headings = soup.find_all(["h2", "h3"])
    for i, h in enumerate(headings):
        section_title = h.get_text(strip=True)
        # Collect sibling content until the next h2/h3.
        parts = []
        for sib in h.find_next_siblings():
            if sib.name in ("h2", "h3"):
                break
            text = sib.get_text(" ", strip=True)
            if text:
                parts.append(text)
        text = " ".join(parts).strip()
        if len(text) < MIN_CHUNK_CHARS:
            continue
        chunks.append({
            "id": f"{advisory_id}::html::section::{i}",
            "advisory_id": advisory_id,
            "tracking_id": advisory_id,
            "title": soup.find("h1").get_text(strip=True) if soup.find("h1") else "",
            "format": "html",
            "section": f"html_section:{section_title.lower().replace(' ', '_')}",
            "section_title": section_title,
            "cve": section_title if section_title.startswith("CVE-") else None,
            "text": text,
        })
    return chunks


def main():
    all_chunks = []
    advisory_dirs = sorted(p for p in ADVISORIES_DIR.iterdir() if p.is_dir())
    for adv_dir in advisory_dirs:
        advisory_id = adv_dir.name
        csaf_path = adv_dir / "csaf.json"
        html_path = adv_dir / "page.html"

        if csaf_path.exists():
            try:
                doc = json.loads(csaf_path.read_text(encoding="utf-8"))
                all_chunks.extend(chunk_csaf(advisory_id, doc))
            except json.JSONDecodeError as e:
                print(f"  [skip] {advisory_id}/csaf.json malformed: {e}")

        if html_path.exists():
            try:
                all_chunks.extend(chunk_html(advisory_id, html_path))
            except Exception as e:
                print(f"  [skip] {advisory_id}/page.html failed to parse: {e}")

    OUT_PATH.write_text(json.dumps(all_chunks, indent=2), encoding="utf-8")
    csaf_n = sum(1 for c in all_chunks if c["format"] == "csaf")
    html_n = sum(1 for c in all_chunks if c["format"] == "html")
    print(f"{len(advisory_dirs)} advisories -> {len(all_chunks)} chunks "
          f"({csaf_n} csaf, {html_n} html) -> {OUT_PATH}")


if __name__ == "__main__":
    main()
