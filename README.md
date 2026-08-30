# CERT@VDE Advisory Pilot

Independent pilot on public data (CERT@VDE advisories + NVD CVE records). Not affiliated with the SRAG
project or its codebase; built to learn the domain before proposing a Research Project at TH OWL / inIT
on SRAG (Security Retrieval Augmented Generation for industrial automation advisories).

This is a bounded experiment that documents precisely where a RAG + entity-resolution pipeline breaks
on real advisory data, weighted toward the project's own stated pain point: product/vulnerability
matching under inconsistent naming.

## Documentation

Read in this order:

| File | What it's for |
|---|---|
| [STATUS.md](STATUS.md) | Continuity notes — current state, what's done, what's not, fastest way back in after a gap. |
| [FAILURE_LOG.md](FAILURE_LOG.md) | The findings themselves, numbered #1–#10, each self-contained with cause and fix. |
| [NARRATIVE.md](NARRATIVE.md) | How the pilot actually unfolded, in story form rather than a status table. |
| [code/component_kb/README.md](code/component_kb/README.md) | The component-knowledge-base prototype specifically. |
| [results_matching.json](results_matching.json), [code/qa/results_qa.json](code/qa/results_qa.json) | Raw scored output behind the headline numbers below. |
| [requirements.txt](requirements.txt) | Pinned Python dependencies — see [Setup & running it yourself](#setup--running-it-yourself). |

## Status (updated 23 August 2026 — see [STATUS.md](STATUS.md) for full continuity notes)

Weeks 1, 3a, and 3b of 4 done. Ingestion paths are **CSAF + HTML only** — CERT@VDE no longer publishes
advisories in PDF (see [FAILURE_LOG.md entry #1](FAILURE_LOG.md#f1)), so the third path from the original plan doesn't exist
against real data.

- **Fetching:** 18 real CERT@VDE advisories (CSAF JSON + HTML), ~75 NVD CVE records.
- **Hand-segmentation (Week 1b):** two real findings — the PDF absence, and a measured 58% vendor-identity
  mismatch between advisories and their own cited NVD records across 72 checked pairs ([FAILURE_LOG.md](FAILURE_LOG.md)
  entries #1–#2).
- **Matching cascade (Week 3a):** exact → part-number → fuzzy, run against a 32-entry synthetic asset
  inventory built from real corpus data. **Precision 0.900, Recall 0.900** on identity matching; a
  separate, lower 33% accuracy on version-range applicability with a specific documented cause
  ([FAILURE_LOG.md entry #3](FAILURE_LOG.md#f3), [results_matching.json](results_matching.json)).
- **Grounded answering + abstention (Week 3b):** hybrid BM25+vector retrieval (Ollama `nomic-embed-text`)
  with RRF fusion and cosine-threshold abstention, generation via Ollama `llama3.1`, scored against 18
  hand-labelled Q&A pairs. **Retrieval hit rate @5 = 1.000, abstention accuracy = 0.944, attribution
  accuracy = 0.929**, zero false answers on deliberately unanswerable questions. Porting the RAG project's
  default abstention threshold unchanged silently failed on this corpus and had to be recalibrated
  ([FAILURE_LOG.md entry #4](FAILURE_LOG.md#f4)). A prompt-injection sanity check resisted one synthetic injection attempt,
  narrowly scoped ([FAILURE_LOG.md entry #6](FAILURE_LOG.md#f6)). Follow-up testing found and fixed a real bug where BM25's
  tokenizer silently broke exact CVE-ID matching — hybrid search's whole reason for existing on
  identifiers ([FAILURE_LOG.md entry #8](FAILURE_LOG.md#f8)) — and separately confirmed a table-formatted remediation section
  can still evade retrieval on vulnerability-phrased questions ([FAILURE_LOG.md entry #7](FAILURE_LOG.md#f7)). Full results in
  [code/qa/results_qa.json](code/qa/results_qa.json).
- **Format comparison (Week 4):** the same 14 answerable questions run against CSAF-only vs. HTML-only
  chunk subsets (no new ingestion — every chunk already carries a `format` tag). **CSAF-only answer
  accuracy 0.857, HTML-only 0.000** — a root-caused, not just measured, failure: CERT@VDE's HTML page
  gives each advisory one Remediation section competing against one section per CVE, so the fix gets
  crowded out of retrieval for multi-CVE advisories ([FAILURE_LOG.md entry #9](FAILURE_LOG.md#f9)).
- **Component-knowledge prototype (23 August):** grows a dictionary of known embedded components
  directly from NVD's own vendor/product field on cross-vendor mismatches, checked against each new
  advisory in real chronological order. **28/42 (66.7%) of mismatches recognized before the advisory
  containing them was processed.** Three real bugs found and fixed during calibration — the last one a
  concrete, measured validation of the debrief's original caution that free-text mining is fragile even
  in a constrained, dictionary-lookup form ([FAILURE_LOG.md entry #10](FAILURE_LOG.md#f10), [code/component_kb/](code/component_kb/)).
- **Not built, deliberately:** the CSAF/HTML ingestion parsers as a standalone reusable module
  (`code/ingestion/` is still scaffolding — both the matching cascade and Slice B work directly against
  raw files without it) and the structured store — neither is needed for any measured number above.
- **All build/measurement work for the pilot is done.** What's left is the memo itself.

## Structure

```
code/
  ingestion/
    csaf/   # empty scaffolding (.gitkeep only) — CSAF 2.0 JSON parsing as a standalone module, not yet built
    html/   # empty scaffolding (.gitkeep only) — HTML advisory scraping/cleaning, not yet built
    pdf/    # empty scaffolding (.gitkeep only) — no real PDF advisories exist in this corpus to build against
  matching/
    flatten_csaf.py   # CSAF product_tree -> flat ProductEntry list; also parses vers: ranges + semver
    cascade.py         # exact -> part-number -> fuzzy (rapidfuzz) matching cascade
    run_matching.py    # scores the cascade against data/synthetic_asset_inventory.json, writes results_matching.json
  qa/
    build_chunks.py            # structure-aware chunking: CSAF doc/vuln notes + CVSS scores, HTML h2/h3 sections
    retrieval.py                # hybrid BM25 + Ollama vector search, RRF fusion, cosine-threshold abstention
    generate_answer.py          # grounded generation (Ollama llama3.1), cites advisory_id/section, refuses on weak evidence
    qa_pairs.json                # 15 hand-labelled Q&A pairs (11 answerable + 4 deliberately unanswerable)
    run_eval.py                  # scores retrieval/abstention/attribution/faithfulness, writes results_qa.json
    prompt_injection_check.py    # Week 3c: one synthetic instruction-bearing chunk, forced into context
    format_comparison.py         # Week 4: same questions against CSAF-only vs HTML-only chunk subsets
  component_kb/
    build_and_eval.py            # grows a component dictionary from NVD's own vendor field, measures recognition
    README.md                    # writeup of this prototype specifically
```

Raw advisory data and NVD pulls are kept locally (not committed here, see [.gitignore](.gitignore)) under `data/`.
[data/synthetic_asset_inventory.json](data/synthetic_asset_inventory.json) **is** committed — it's original constructed test data, not scraped
third-party content, and it's the ground truth the matching numbers above are measured against.

## Setup & running it yourself

Requires Python 3.11+ and, for the Slice B / grounded-answering pieces only, a local
[Ollama](https://ollama.com) instance.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# only needed for retrieval.py / generate_answer.py / run_eval.py / format_comparison.py:
ollama pull nomic-embed-text
ollama pull llama3.1
ollama serve   # if not already running, listens on http://localhost:11434
```

Reproduce the headline numbers:

```bash
cd code/qa && python3 run_eval.py                   # Slice B retrieval/abstention/attribution, <1min
cd code/qa && python3 format_comparison.py           # CSAF-only vs HTML-only accuracy
```

These two need `ollama serve` running (models above pulled) but nothing else — they read from
[code/qa/chunks.json](code/qa/chunks.json), which **is** committed; `embeddings.json` is gitignored and
regenerates automatically (~10s) on first `retrieval.py` call if missing.

```bash
cd code/matching && python3 run_matching.py         # Precision/Recall on identity matching
cd code/component_kb && python3 build_and_eval.py    # component-recognition rate
```

These two need no Ollama, but **will not run on a fresh clone** — both read the 18 advisories' raw
`csaf.json` files from `data/advisories/`, which is deliberately gitignored (raw third-party content) and
has no fetch script committed here (see [STATUS.md](STATUS.md), "Fetching"). Re-fetch the same 18
CERT@VDE advisories and matching NVD records into `data/advisories/<ID>/csaf.json` /
`data/nvd/<CVE>.json` before running either script. [data/synthetic_asset_inventory.json](data/synthetic_asset_inventory.json)
is the one exception — it's original constructed test data, not scraped, and is committed.

## Roadmap

Four-week build: ingestion (Week 1-2) → structured + vector store (Week 2) → entity-resolution matching
and grounded answering with abstention (Week 3) → format comparison and failure log (Week 4). Actual
order diverged from this once real data made Week 3a's measurement available early — see [STATUS.md](STATUS.md).
