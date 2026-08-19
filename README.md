# SRAG Pilot

A small, honest pilot on public security-advisory data (CERT@VDE advisories + NVD CVE records), built to
learn the domain before proposing a Research Project at TH OWL / inIT on SRAG (Security Retrieval
Augmented Generation for industrial automation advisories).

This is not SRAG. It is a bounded experiment that documents precisely where a RAG + entity-resolution
pipeline breaks on real advisory data, weighted toward the project's own stated pain point:
product/vulnerability matching under inconsistent naming.

## Status (updated 19 August 2026 — see `STATUS.md` for full continuity notes)

Weeks 1 and 3a of 4 done, ahead of the ingestion build (Week 2) because Week 1b's hand-segmentation
surfaced findings worth measuring immediately rather than waiting. Ingestion paths are **CSAF + HTML
only** — CERT@VDE no longer publishes advisories in PDF (see `FAILURE_LOG.md` entry #1), so the third
path from the original plan doesn't exist against real data.

- **Fetching:** 18 real CERT@VDE advisories (CSAF JSON + HTML), ~75 NVD CVE records.
- **Hand-segmentation (Week 1b):** two real findings — the PDF absence, and a measured 58% vendor-identity
  mismatch between advisories and their own cited NVD records across 72 checked pairs (`FAILURE_LOG.md`
  entries #1–#2).
- **Matching cascade (Week 3a, built early):** exact → part-number → fuzzy, run against a 32-entry
  synthetic asset inventory built from real corpus data. **Precision 0.900, Recall 0.900** on identity
  matching; a separate, lower 33% accuracy on version-range applicability with a specific documented
  cause (`FAILURE_LOG.md` entry #3, `results_matching.json`).
- **Not yet built:** the CSAF/HTML ingestion parsers themselves (`code/ingestion/` is still scaffolding),
  the structured store, grounded answering + abstention (Slice B), the prompt-injection check.

## Structure

```
code/
  ingestion/
    csaf/   # CSAF 2.0 JSON parsing, structured (not flattened) product tree — not yet built
    html/   # HTML advisory scraping/cleaning — not yet built
    pdf/    # kept as reference only; no real PDF advisories exist in this corpus to build against
  matching/
    flatten_csaf.py   # CSAF product_tree -> flat ProductEntry list; also parses vers: ranges + semver
    cascade.py         # exact -> part-number -> fuzzy (rapidfuzz) matching cascade
    run_matching.py    # scores the cascade against data/synthetic_asset_inventory.json, writes results_matching.json
```

Raw advisory data and NVD pulls are kept locally (not committed here, see `.gitignore`) under `data/`.
`data/synthetic_asset_inventory.json` **is** committed — it's original constructed test data, not scraped
third-party content, and it's the ground truth the matching numbers above are measured against.

## Roadmap

Four-week build: ingestion (Week 1-2) → structured + vector store (Week 2) → entity-resolution matching
and grounded answering with abstention (Week 3) → format comparison and failure log (Week 4). Actual
order diverged from this once real data made Week 3a's measurement available early — see `STATUS.md`.
