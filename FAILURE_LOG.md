# Failure Log

Running log per `SRAG Preparation Roadmap.md` Phase 6: what happened, why, and what it would take to fix. Raw material for the research project proposal — three or four good failure modes give a defensible, bounded topic.

---

## 1. CERT@VDE no longer publishes advisories in PDF format

**What I asked:** pulled every format available for 18 real CERT@VDE advisories (VDE-2026-005 through VDE-2026-085), per Week 1's fetching goal (CSAF JSON, HTML, PDF where present).

**What happened:** every single advisory came back with `csaf.json` and `page.html` — zero PDFs across all 18. Confirmed by directory listing, not a fetch bug on my end.

**Why I think this happened:** CERT@VDE appears to have moved to CSAF JSON + HTML as its exclusive current publication format, dropping PDF entirely (or PDF is reserved for legacy/archived advisories predating the migration — not confirmed either way).

**What it would take to fix / what this changes:**
- The planned three-way ingestion comparison (CSAF / HTML / PDF) is a two-way comparison in practice for this data source. Getting a genuine third PDF path would require either digging into CERT@VDE's older archive (if one exists) or pulling PDF-format advisories from a different vendor source (e.g. Siemens ProductCERT).
- Decided not to chase this — Slice A (format robustness) is already the least distinctive of the three candidate slices per the roadmap, and this finding is itself a reason to deprioritize it further rather than a gap to backfill.
- `extract_report.py` in `code/ingestion/pdf/` stays as a reference OCR capability (built for an unrelated TÜV audit-report task), not something being adapted toward advisory PDFs specifically, since there's no real target to adapt it against.
- **Worth stating directly in the memo's honesty section:** this is a genuine, verifiable observation about where the industry's actual publishing practice stands today, not a limitation of the pilot — and it lines up with the CSAF paper's own finding (Wunder et al., EuroUSEC 2024) that CSAF adoption is still uneven industry-wide.

---

## 2. CPE-to-CPE matching fails even for a single, correctly-parsed CVE — the advisory and its own NVD record describe different supply-chain layers

**What I asked:** hand-segmented VDE-2026-005 (ifm: Multiple Vulnerabilities in CR3171) per Week 1b, then pulled the NVD record for one of its three referenced CVEs (CVE-2025-41691) to compare product identity across sources.

**What happened:** ifm's CSAF advisory names the affected product as **"ifm CR3171"** (`cpe:2.3:o:ifm_electronic:cr3171_firmware:*`). NVD's own record for the *same CVE* names the affected vendor/product as **"CODESYS"**, across 15 product variants (Control RTE (SL), Control Win (SL), HMI (SL), Control for PFC200 SL, etc.), with version ranges like `3.5.21.10` to `<3.5.21.20`. There is zero overlap — no shared CPE, no shared vendor string, no shared product name — between ifm's advisory and NVD's own affected-products list for the CVE ifm itself cites.

**Why this happened:** CODESYS is a third-party runtime component embedded inside ifm's CR3171 firmware. NVD tracks the vulnerability at the component level (where the flaw actually lives); CERT@VDE/ifm tracks it at the end-product level (what the customer actually owns). The only bridge between the two is a sentence of free prose inside ifm's advisory ("impacted by various CODESYS vulnerabilities") — not a structured identifier anywhere.

**What it would take to fix / what this changes:**
- This is a stronger, more concrete version of the SBOM layer already named in the domain chain (`SRAG-Introduction TO Phase-I.md`) — not hypothetical, found in the first pair of real files examined.
- **Structured CPE-to-CPE matching between a CSAF advisory and its own cited NVD record is not sufficient on its own**, even with a correctly parsed CVE ID and no aliasing/typo involved. This is a different failure mode than the SIMATIC/Ctrl-X worked example (which is about naming variance for the *same* product) — this is about *different products at different supply-chain layers* sharing a CVE.
- A fix would need either: (a) mining the free-text notes for component names (fragile, exactly the "semantic similarity is dangerous" problem from the debrief), or (b) an actual SBOM for CR3171 stating "contains CODESYS Control RTE 3.5.21.x" as a structured fact — which per Foster et al. (literature log entry #2), inIT doesn't yet have a standardized AAS submodel for either.
- **Worth leading with in the memo** — arguably stronger than the illustrative example, since it's real, current data and shows the matching problem surviving even when the "easy" part (parsing the right CVE ID) is already solved correctly.

**Update, 19 August — confirmed as the dominant pattern, not a one-off.** Ran the same vendor-comparison across all 72 CVE-to-advisory pairs in the collected corpus where an NVD record exists (`code/ingestion/` — ad hoc script, not yet a permanent module). Results:

| | Count | % |
|---|---|---|
| Advisory vendor matches NVD's own listed vendor | 23 | 32% |
| Genuine different-vendor mismatch (embedded component / OEM) | 42 | 58% |
| NVD has no structured vendor/product data at all for that CVE | 7 | 10% |

**The 42 mismatches are overwhelmingly the embedded-component pattern, not noise:**
- Mettler-Toledo's advisory (VDE-2026-066, 10 CVEs) is entirely Microsoft CVEs — the device runs Windows.
- Balluff's advisory (VDE-2026-049, 20 CVEs) spans Grafana, OpenSSL, Go toolchain, Red Hat, Cloudflare, perl — looks like a Grafana-based monitoring appliance with its whole software stack exposed in one bulletin.
- ads-tec's advisory (VDE-2026-076, 15 CVEs) bundles OpenSSL, Siemens, dnsmasq, OpenSC.
- ifm/CODESYS (entry above) is one instance of this same pattern, not a special case.
- **A distinct OEM variant:** Helmholz (VDE-2026-070) and MB connect line (VDE-2026-068) both cite the identical CVE-2026-10521 — Helmholz appears to rebrand MB connect line's hardware under its own name.

**Separately, a data-quality note:** `data/nvd/CVE-2026-35078.json` is malformed/empty — JSON parse error on load. Worth handling as a skip-don't-abort case in the real ingestion code (per the retry/skip-don't-abort pattern already used in the data-engineering background), not a crash.

**Why this changes the memo, not just the failure log:** exact or fuzzy CPE matching between a CSAF advisory and its own cited NVD record fails for the *majority* (58%) of real references in this corpus — not an edge case. This is a measured number from real data, which is exactly what Week 3's matching pipeline was meant to eventually produce, arriving three weeks early as a side effect of doing Week 1b properly. Strongly worth leading the memo's "what I found" section with this number instead of, or alongside, the illustrative SIMATIC/Ctrl-X example.

---

## 3. The full matching cascade, run — Precision 0.900 / Recall 0.900, and one distinct version-range problem underneath it

**What I built:** a real exact → part-number → fuzzy cascade (`code/matching/`) run against a 32-entry synthetic asset inventory constructed from real vendor/product/version/part-number data across the 18 collected advisories (`data/synthetic_asset_inventory.json`), not invented from scratch. Full results in `results_matching.json`.

**Identity-matching result:** TP=18, FP=2, FN=2, TN=10 → **Precision 0.900, Recall 0.900**. Breakdown by resolving stage: fuzzy 10, part-number 7, exact 1 — most real matches needed more than exact string equality, confirming the premise rather than assuming it.

**The false positives are the demonstrated version of the debrief's worked-example risk, not a hypothetical:**
- A fictional "PROCON-WEB SCADA XT" (constructed specifically to test this) fuzzy-matched the real PROCON-WEB SCADA advisory at score 95 — the SIMATIC-Controller-X-vs-XT false positive from the debrief, reproduced with real product-name shapes rather than argued abstractly.
- A fictional "MBS UGW-Y-Series" scored 85.5 against the real MBS advisory — vendor genuinely correct, specific product line wrong. A sharper variant of the same risk: correct vendor identification isn't the same as correct product identification.

**Two implementation issues caught and fixed before trusting these numbers (both real, both worth naming precisely if asked how the cascade works):**
1. `rapidfuzz`'s `WRatio` alone produced a spurious 85.5 score matching a short 5-word query against an unrelated 12-word product string, purely from character-level partial overlap. Fixed with a `token_set_ratio >= 60` guard — the spurious case scored 35.6 on that metric versus 78–100 for every genuine match in the corpus, a clean separation, not an arbitrary tune.
2. Cascade stage ordering originally tried fuzzy before part-number, letting a mediocre-but-passing fuzzy score pre-empt a correct, higher-confidence part-number match. Reordered to exact → part-number → fuzzy, consistent with the debrief's own stated confidence ranking ("part number... near-certain, better than any fuzzy text").

**Version-range accuracy is a separate, lower number — 5/15 (33%) — with a specific, understood cause, not a general matching failure:** in advisories that model firmware-installed-on-hardware relationships (e.g. ifm's CR3171, VDE-2026-005), CVE-applicability data (`product_status.known_affected`) is attached only to the synthetic "Firmware X installed on Y" composite product entries — never to the bare "Firmware" version-branch entries that name-based matching naturally favors, since those are shorter and score better on string similarity. The cascade correctly identifies *which product* an asset is, but can silently resolve to the wrong *specific CSAF entry* for determining whether a given version is actually affected. This is a distinct difficulty layer from identity matching itself — discovered by running the code against real data, not something that could have been predicted from reading the spec.

**What it would take to fix:** when multiple product-tree entries tie or are close in name-similarity within the same advisory, prefer the one whose entries actually carry `cve_ids` — a principled tie-breaker, not implemented yet given the time budget. Documenting this precisely is more valuable than a quietly-inflated number.

**For the memo:** lead with 0.900/0.900 on identity matching as the headline number — it's real, it's measured, and the two false positives it did produce are the exact dangerous pattern the project's own stated problem describes. Report the version-range gap as a distinct, honestly-scoped finding, not folded into the headline number.
