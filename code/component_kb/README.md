# Component knowledge base

Prototype for one of the two paths named in `FAILURE_LOG.md` entry #2's honesty note: mining
advisory free text for embedded-component names (e.g. recognizing that an ifm advisory citing a
CODESYS CVE is describing an embedded runtime, not a naming inconsistency). The alternative path —
an SBOM/AAS submodel stating the same fact structurally — needs data that doesn't exist yet
(per Foster et al., see the literature log), so this is the one that could actually be built now.

Not open-vocabulary NER. Grows a dictionary of known components directly from NVD's own
vendor/product field on cross-vendor mismatches already found (`FAILURE_LOG.md` #2), then checks
each new advisory's own CVE text against that dictionary with strict word-boundary matching.

```
build_and_eval.py         processes all 18 advisories in chronological order, measures recognition
component_knowledge.json  the grown dictionary (component -> aliases seen, occurrences)
results_component_kb.json per-mismatch detail: recognized or novel, and by which dictionary entry
```

**Result:** 28 of 42 genuine cross-vendor mismatches (66.7%) were recognized by a dictionary built
purely from earlier advisories, before the advisory containing them was processed. Full detail,
including three real bugs found and fixed during calibration, in `FAILURE_LOG.md` entry #10.

Run: `python3 build_and_eval.py` (no Ollama needed, pure Python + `rapidfuzz`).
