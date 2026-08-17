# Auditbericht OCR Extractor

Turns a TÜV NORD "Auditbericht (Stufe 2)" PDF (form template **A00F207**) into a
structured JSON file, entirely on the local machine — no network calls, no
third-party services. Built for `Auditbericht.pdf` and designed to work
unmodified on future audit reports that use the same form.

## Why this needs OCR at all

The source PDF has **no text layer**. Every page is a single flattened raster
image (confirmed via `pdfplumber`: 0 characters, 0 vector objects on every
page). That rules out any of the normal "just extract the text" approaches
(`pdfplumber`, `PyMuPDF` text extraction, `pypdf`, etc.) — there is no text to
extract, only pixels. So the pipeline has to actually *read* the page the way
a person would: render it, then OCR it.

## Pipeline

```
PDF page
  │  PyMuPDF, 300 DPI
  ▼
grayscale image
  │  OpenCV: detect + paint out horizontal/vertical table rule lines
  ▼
cleaned image  ──────────────►  Tesseract (German) ──► text / word boxes
  │
  │  OpenCV: crop known cells, measure ink density
  ▼
checkbox states
  │
  ▼
regex / row-anchor parsing  ──►  JSON matching Auditbericht.json's schema
```

**Step 1 — render.** Each page is rasterized at 300 DPI with PyMuPDF
(`fitz`). Higher DPI than the PDF's native resolution measurably improved OCR
accuracy during tuning.

**Step 2 — strip the table grid.** This turned out to be the single most
important step. Tesseract fed the raw page image drops or badly mangles a
large fraction of table cells — the ruled borders confuse its layout
segmentation. `remove_grid_lines()` detects long horizontal/vertical runs of
ink via morphological erode/dilate (`detect_line_masks`) and paints them
white before OCR. Going from "OCR the raw page" to "OCR the grid-stripped
page" took whole sections (e.g. the `Stammdaten der Organisation` table) from
mostly-empty to essentially perfect.

**Step 3 — OCR.** `pytesseract`, German language pack, mostly PSM 4 (assumes
a single column of text of variable size — a good fit for this form's mostly
one-table-per-page layout). `ocr_words()` additionally exposes per-word
bounding boxes via Tesseract's `image_to_data`, used wherever row/column
*position* matters more than reading order (the numbered tables, the
checkbox matrix).

**Step 4 — checkboxes.** Two different techniques, chosen per situation:

- *Text heuristic* (`checkbox_state`, `prefix_has_checkmark`): for a
  checkbox immediately followed by its own label on the same line (e.g. `☒
  Ja  ☐ Nein  ☐ n.z.`), Tesseract reliably renders a checked box as an
  uppercase `X` glued onto adjacent noise characters (`X]`, `IX]`, `DX]`,
  `&X]`) and an unchecked box as bracket noise with no `X` (`[_]`, `[]`,
  `D`). Matching is deliberately restricted to *uppercase* `X` only —
  several German labels contain a lowercase `x` organically (`WebEx`), which
  would otherwise false-positive.
- *Pixel ink-density* (`ink_ratio`): for grid checkboxes with no adjacent
  text to anchor to (the final ISO 9001/14001/…/… outcome matrix on page 15,
  the "Beigefügt" column on page 6), each cell is cropped by its known row
  y-band and column x-band (from the detected vertical rule lines) and
  scored by the fraction of dark pixels inside, after insetting past the
  cell border. A checked cell is a clear ink-density outlier relative to the
  other cells in the same row (page 15) or column (page 6).

**Step 5 — parsing.** Mostly label-anchored regex over the cleaned OCR text
(`label_value`, `find_line`). A few sections needed more than that:

- *Auditergebnis* (the ISO 9001/14001 chapter-by-chapter results table, page
  5): the small `"4.1"`-style chapter numbers get mangled unpredictably by
  OCR (missing dot, stray digits/letters). Rather than trust OCR to read the
  chapter number, the chapter list is hardcoded from the *published
  standards* (`ISO_9001_2015_CHAPTERS`, `ISO_14001_2015_CHAPTERS` — these
  don't change between audits) and matched by row order; only the 0–3/`-`
  result score is actually read off the page, using a regex that requires a
  number-like prefix before the score digit to avoid mistaking the "1" in
  "4.1" for a result value.
- *Numbered multi-column tables* (Verbesserungspotenziale, Positive
  Aspekte, Anmerkungen — pages 12–14): each row's "Nr." is used as an
  anchor. Column x-bands come from the detected vertical rule lines; each
  column is OCR'd independently (`image_to_data`) so a row's cells can be
  reassembled by y-position even though the columns have very different
  line-wrap counts. Row-numbering tolerates a **gap** rather than requiring
  an exact `n, n+1, n+2, …` sequence, because a single misread digit (e.g.
  "13" read as "19") would otherwise permanently desync the rest of the
  table — the next *correctly*-read number just resynchronizes instead of
  everything after it being silently dropped. These tables also span a page
  boundary without repeating their header, so extraction scans a list of
  candidate pages and stops the last row at the next table's header
  (`stop_pattern`) rather than running to the physical bottom of the page.

## Files

- `extract_report.py` — the extractor. Self-contained, one file.
- `Auditbericht.json` — hand-verified ground truth (built by reading the PDF
  directly), used during development to validate the OCR pipeline's output
  field-by-field.
- `Auditbericht.pdf` — source document (confidential — see note below).

## Setup

```bash
brew install tesseract-lang        # adds the German OCR language data
pip3 install PyMuPDF pytesseract opencv-python-headless Pillow numpy
```

(`tesseract` itself, and `pdfplumber` used only during investigation, are
assumed already present / not required at runtime.)

## Usage

```bash
python3 extract_report.py <input.pdf> [output.json] [--debug]
```

- If `output.json` is omitted, it defaults to `<input>.json` next to the
  PDF — **be careful with this on `Auditbericht.pdf` itself**, since that
  would overwrite the hand-verified `Auditbericht.json`. Pass an explicit
  output path when in doubt.
- `--debug` writes `<output>_debug/` alongside the result: the grid-stripped
  image and raw OCR text for every page. Useful for tuning a regex against
  a new file, or for spot-checking a field that looks wrong.

## Output shape

One JSON object per report, sectioned to mirror the PDF's own headings:
`stammdaten_der_organisation`, `auditprofil`, `auditierte_standards`,
`audit_details`, `remote_audit`, `auditergebnis`, `pflichtelemente_a00va02`,
`firmenprofil`, `zusammenfassung` (includes the parsed subsidiary list),
`schlussfolgerungen`, `nichtkonformitaeten`, `verbesserungspotenziale` /
`positive_aspekte` / `anmerkungen` (the three numbered tables), and
`abschluss_und_empfehlungen`. See `Auditbericht.json` for a concrete example
of every field.

Every run's `extraction_meta` records how it was produced
(`extraction_method: "local_ocr"`, engine, language, DPI, timestamp) so a
future reader can tell an OCR'd result apart from a hand-verified one.

## Accuracy (validated against the hand-built ground truth)

- Organization/profile/dates/remote-audit fields, all checkbox groups
  (Ja/Nein grids, the page-15 outcome matrix): effectively exact.
- The 50-cell ISO 9001/14001 results table: 49/50 exact; the one miss is
  reported as `null` rather than a wrong guess (the source glyph OCR'd as an
  unrecoverable stray letter).
- Subsidiary list, summary narratives, nonconformities table: exact.
- The three itemized tables: 20/20, 15/16, 5/7 rows recovered — a handful of
  rows lose their row boundary when a row-number digit is misread badly
  enough that even the gap-tolerant matching can't place it, and its content
  merges into a neighboring row.

Treat this as a strong first-pass extraction to spot-check, not a
guaranteed-perfect one — it's OCR on a scanned document, not a text-layer
read. `--debug` output is there for exactly that spot-checking.

## Extending to a new report template

Everything in `ReportExtractor` is organized one method per PDF section
(`extract_stammdaten`, `extract_auditierte_standards`, …), each calling
`self.page_lines(n)` for the OCR'd text of a given 0-indexed page. If a
future report reorders sections or shifts them to different page numbers,
update the page index passed into each `extract_*` call in
`build_report_json()`. If the *labels* themselves change wording, the
`label_value(...)` regex patterns are the place to adjust. The lower-level
utilities (`remove_grid_lines`, `ink_ratio`, `vertical_line_centers`,
`extract_numbered_table`) are not tied to this specific form and should work
unchanged on a differently-labeled table with the same visual structure
(ruled grid, one numbered item per row, etc.).

## Confidentiality note

`Auditbericht.pdf` and `Auditbericht.json` contain real, confidential audit
findings for Lödige Industries GmbH. Nothing in this pipeline uploads that
data anywhere — rendering, OCR, and parsing all happen locally. Keep it that
way if this project is shared further (e.g. don't wire `extract_report.py`
into a hosted service without reconsidering that).
