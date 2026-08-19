# Status — continuity notes

*Last updated 19 August 2026. Read this first if picking the pilot back up after a gap — it's written
so the next session doesn't need to re-derive anything from the conversation history.*

Companion docs, outside this repo, in `research paper/inIT/`:
- `SRAG Kickoff Plan (4 Weeks).md` — the week-by-week plan this repo is executing.
- `SRAG Preparation Roadmap.md` — the full method behind the plan.
- `SRAG-Introduction TO Phase-I.md` — current understanding, evidence inventory, open research question.
- `SRAG Contingency Plan.md` — scenario tree for the Trsek outreach, memo structure.
- `# Literature.md` — reading log, six papers read and verified.

**Target: send the Trsek memo early September 2026.**

---

## What's actually done, in build order (not calendar order — some things happened early)

1. **Literature (Week 0).** Six papers read and verified against source PDFs (not just abstracts) —
   Gebauer et al. (SUSI predecessor), Foster/Moriz/Trsek (AAS modeling), BEACON (budget-aware entity
   matching), the CSAF practitioner study (Wunder et al. — has the strongest quote in the whole project),
   the LLM-CVE-reliability study (Abdullah et al.), AgenticRAG (Microsoft). One citation-misattribution
   caught and fixed along the way. Benndorf paper deliberately deferred; Benndorf outreach dropped from
   the plan entirely (see Contingency Plan).

2. **Fetching (Week 1a).** 18 real CERT@VDE advisories under `data/advisories/<ID>/` (`csaf.json`,
   `page.html`, `meta.json` each), ~75 NVD CVE records under `data/nvd/`. `data/` is gitignored (raw
   third-party content); this data is reproducible via the fetch process, not re-derivable from this repo
   alone if lost — re-fetch from CERT@VDE + the NVD API if `data/` is ever missing locally.

3. **Hand-segmentation (Week 1b) — this is where the real findings came from.** Manually read several
   advisories' raw JSON structure before writing any parser. Found:
   - **No PDF advisories exist in the current CERT@VDE corpus at all** (`FAILURE_LOG.md` #1) — the
     original three-path ingestion plan (CSAF/HTML/PDF) is a two-path plan against real data.
   - **58% of CVE-to-advisory pairs show a genuine vendor-identity mismatch** between the advisory and
     NVD's own record for the same CVE (`FAILURE_LOG.md` #2) — usually because the advisory's vendor
     ships a product embedding a different vendor's component (CODESYS, Grafana, OpenSSL, Windows, etc.).
     This is the single strongest measured finding in the whole project so far.

4. **Matching cascade (Week 3a, built ahead of Week 2's ingestion).** `code/matching/`:
   - `flatten_csaf.py` — turns any advisory's `product_tree` into a flat list of matchable product
     entries, handling both simple vendor→family→product→version trees and the `installed_on`
     relationship pattern (firmware-on-hardware) that creates synthetic combined product IDs. Also
     parses CSAF's `vers:generic/>=A|<=B` range syntax and plain/semver-ish exact versions. Tested
     against all 18 real advisories without crashing; one real bug (dropped bare hardware-only leaf
     entries with no version branches) caught and fixed by spot-checking output, not by assumption.
   - `cascade.py` — exact → part-number → fuzzy (in that priority order; part-number outranks fuzzy
     per the debrief's own "near-certain, better than any fuzzy text" reasoning — an earlier ordering
     had this backwards and it silently cost real matches). Fuzzy stage uses `rapidfuzz.fuzz.WRatio`
     gated by a `token_set_ratio >= 60` floor (guards against WRatio's partial-ratio component scoring
     short queries as spurious high matches against long, unrelated product strings — verified: the
     spurious case scored 35.6 on token_set_ratio vs 78-100 for every genuine match).
   - `run_matching.py` — scores the cascade against `data/synthetic_asset_inventory.json` (32 entries,
     built from real corpus vendors/products/versions/part-numbers, not invented), writes
     `results_matching.json`.
   - **Result: Precision 0.900, Recall 0.900** (TP=18, FP=2, FN=2, TN=10) on identity matching. The two
     false positives are live-reproduced versions of the debrief's SIMATIC-X-vs-XT risk. **Version-range
     accuracy is separately measured at 33%**, with a specific documented cause (`FAILURE_LOG.md` #3):
     CVE-applicability data in relationship-modeled advisories lives only on the synthetic
     "installed_on" composite entries, not the bare component entries name-matching naturally favors.

---

## What's NOT done yet

- **Week 2: the actual ingestion pipeline.** `code/ingestion/csaf/` and `code/ingestion/html/` are still
  empty scaffolding. The matching cascade above works directly against raw `csaf.json` files — it does
  not depend on the ingestion pipeline existing, so this can be built in parallel or after without
  blocking anything already measured.
- **The structured store** (`advisories`/`cves`/`products`/`assets`/`chunks` tables) — not started. Per
  the "lean scripts, not a full product" decision (19 August), this may not be needed before the memo at
  all — the measured numbers above came entirely from flat JSON + Python, no database.
- **Slice B: grounded answering + abstention.** Not started. Plan is to reuse the RAG project's hybrid
  BM25+vector search and cosine-threshold abstain pattern (`README_RAG.md` in the inIT folder), pointed
  at advisory chunks instead of generic PDFs. Needs: chunking the 18 advisories' text, ~10-15 hand-labeled
  Q&A pairs (scaled down from the roadmap's 20-30 given the compressed timeline), the actual retrieval
  code.
- **Prompt-injection sanity check** (Week 3c) — cheap, ~10 minutes, not done yet. Insert one advisory
  with an instruction-like sentence, see if the system obeys it.
- **`code/ingestion/pdf/extract_report.py`** — this is the TÜV NORD OCR extractor from an unrelated
  student-job task, sitting in this folder as a reference/placeholder since no real PDF advisories exist
  to build a genuine PDF ingestion path against. Decide before the memo whether to keep it as a "here's
  evidence I can handle messy PDF extraction generally" artifact or remove it as out of scope.

---

## Known limitations to carry forward, not re-discover

- `data/nvd/CVE-2026-35078.json` is malformed/empty (JSON parse error). Needs skip-don't-abort handling
  in any future ingestion code, not a crash.
- Some advisory tracking IDs don't match their folder name derived from the CERT@VDE URL (e.g. the
  `VDE-2026-019` folder's internal `tracking.id` is `PPSA-2026-002`; `VDE-2026-040`'s is
  `Advisory2026-04_VDE-2026-040`). `flatten_csaf.py` and the synthetic inventory's ground truth both use
  the internal `tracking.id`, not the folder name — stay consistent with that if adding more code.
- The version-range parser (`flatten_csaf.parse_version_spec`) does digit-extraction-based comparison
  (`re.findall(r"\d+", v)` then tuple comparison) for non-`vers:`-prefixed specs — works for the
  letter-prefixed pseudo-semver formats seen in this corpus (e.g. `V6_00_07`) but hasn't been stress-
  tested beyond what's in the current 18-advisory corpus.

---

## If resuming: fastest way back in

1. `cd code/matching && python3 run_matching.py` reproduces the headline numbers in ~2 seconds — good
   first move to confirm nothing's drifted.
2. Read `FAILURE_LOG.md` top to bottom — three entries, each self-contained, each with "what it would
   take to fix" already written out.
3. The two-week plan (from the 19 August planning conversation): synthetic inventory + cascade (done,
   this file) → minimal grounded-answering slice → consolidate failure log → draft the memo → send early
   September.
