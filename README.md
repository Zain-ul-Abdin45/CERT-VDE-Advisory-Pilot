# SRAG Pilot

A small, honest pilot on public security-advisory data (CERT@VDE advisories + NVD CVE records), built to
learn the domain before proposing a Research Project at TH OWL / inIT on SRAG (Security Retrieval
Augmented Generation for industrial automation advisories).

This is not SRAG. It is a bounded experiment that documents precisely where a RAG + entity-resolution
pipeline breaks on real advisory data, weighted toward the project's own stated pain point:
product/vulnerability matching under inconsistent naming.

## Status

Week 1 of 4 — fetching, understanding, segmenting. See `code/ingestion/` for the three parallel
ingestion paths (CSAF JSON, HTML, PDF), kept separate so effort-per-format is directly comparable.

## Structure

```
code/
  ingestion/
    csaf/   # CSAF 2.0 JSON parsing, structured (not flattened) product tree
    html/   # HTML advisory scraping/cleaning
    pdf/    # PyMuPDF-based PDF extraction
```

Raw advisory data, NVD pulls, and the synthetic asset inventory are kept locally (not committed here)
under `data/`, versioned with a provenance README of their own.

## Roadmap

Four-week build: ingestion (Week 1-2) → structured + vector store (Week 2) → entity-resolution matching
and grounded answering with abstention (Week 3) → format comparison and failure log (Week 4).
