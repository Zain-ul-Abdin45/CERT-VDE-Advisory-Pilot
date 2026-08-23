"""
Prototype + measurement for the component-knowledge idea: instead of NER on
free text, grow a dictionary of known embedded components from NVD's own
vendor field on cross-vendor CVE mismatches (FAILURE_LOG.md #2), then check
each new advisory's free text against that dictionary with fuzzy matching
(rapidfuzz, same tooling as code/matching/cascade.py) rather than exact
string match.

Processes advisories in real chronological order (document.tracking.
initial_release_date) to simulate the dictionary actually growing over time,
one advisory at a time, rather than a single fixed train/test split.

Output: component_knowledge.json (the grown dictionary) and
results_component_kb.json (per-advisory: whether each mismatch was already
recognized by the dictionary as it stood *before* that advisory was
processed, or was novel and had to be added).
"""
import json
import re
from collections import Counter
from pathlib import Path

from rapidfuzz import fuzz

REPO = Path(__file__).resolve().parents[2]
ADVISORIES_DIR = REPO / "data" / "advisories"
NVD_DIR = REPO / "data" / "nvd"
HERE = Path(__file__).resolve().parent


def normalize(name: str) -> str:
    return name.strip().lower()


def load_advisories_chronological():
    rows = []
    for adv_dir in sorted(ADVISORIES_DIR.iterdir()):
        if not adv_dir.is_dir():
            continue
        csaf_path = adv_dir / "csaf.json"
        if not csaf_path.exists():
            continue
        try:
            doc = json.loads(csaf_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        release_date = doc["document"]["tracking"]["initial_release_date"]
        rows.append((release_date, adv_dir.name, doc))
    rows.sort(key=lambda r: r[0])
    return rows


def vulnerability_text(v: dict) -> str:
    # Scoped to THIS CVE's own notes only - concatenating every CVE's notes
    # into one advisory-wide blob (an earlier version of this script did
    # that) let one CVE's component name (e.g. "Grafana", mentioned 20 times
    # across a multi-CVE advisory) leak into the recognition check for
    # unrelated CVEs in the same advisory (Go toolchain, Cloudflare, perl),
    # producing false "recognized" tags. Checked the raw data first: each
    # CVE's own description note does correctly name its own component
    # ("The go command may execute unexpected commands..." for the Go CVE),
    # so scoping fixes it rather than papering over a genuine fragility.
    return " ".join(n.get("text") or "" for n in v.get("notes", []))


# Generic technical words that could appear as a repeated product-name
# prefix (e.g. CODESYS's "Control RTE (SL)" / "Control Win (SL)") but are
# too common across unrelated CVE descriptions to use as a recognition
# signal without reintroducing the same partial_ratio fragility documented
# above - "Control" would spuriously match almost anything about access
# control or a PLC controller.
GENERIC_PRODUCT_WORD_STOPLIST = {
    "control", "service", "system", "software", "server", "client",
    "manager", "device", "module", "runtime", "platform", "application",
}


def nvd_affected_for_cve(cve_id: str) -> list[tuple[str, str]]:
    """Return [(vendor, product), ...] for a CVE, skipping NVD's literal
    "n/a" placeholder (no structured data) - a distinct category from a
    genuine cross-vendor mismatch (FAILURE_LOG.md #2's 3-way split)."""
    path = NVD_DIR / f"{cve_id}.json"
    if not path.exists():
        return []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    pairs = []
    for v in d.get("vulnerabilities", []):
        for affected in v.get("cve", {}).get("affected", []):
            for ad in affected.get("affectedData", []):
                vendor = ad.get("vendor")
                product = ad.get("product") or ""
                if vendor and vendor.strip().lower() != "n/a":
                    pairs.append((vendor, product))
    return pairs


def product_family_aliases(products: list[str]) -> list[str]:
    """A vendor's advisory sometimes names the PRODUCT ("Windows") rather
    than the parent VENDOR ("Microsoft") - found by checking why a real
    Microsoft/Windows mismatch wasn't recognized even with "Microsoft" in
    the dictionary (its CVE notes say "Windows Routing and Remote Access
    Service...", never the word "Microsoft"). Full versioned product
    strings ("Windows 10 Version 1607") don't partial_ratio-match well
    against unrelated free text (52.2, below threshold) but their shared
    leading word does (100.0) - so extract a leading word only when it
    repeats across >=2 of this vendor's product variants (a data-driven
    signal that it's a genuine brand/family name, not a one-off), is
    capitalized, and isn't a generic technical term."""
    first_words = Counter()
    for p in products:
        word = p.split()[0] if p.split() else ""
        if len(word) >= 4 and word[0].isupper() and word.lower() not in GENERIC_PRODUCT_WORD_STOPLIST:
            first_words[word] += 1
    return [w for w, count in first_words.items() if count >= 2]


def is_vendor_match(advisory_vendor: str, nvd_vendor: str) -> bool:
    return fuzz.token_set_ratio(normalize(advisory_vendor), normalize(nvd_vendor)) >= 70


def dictionary_recognizes(free_text: str, dictionary: dict) -> str | None:
    """Return the matched dictionary key if the free text mentions a known
    component name, else None.

    This went through three real, increasingly-instructive bugs while
    calibrating it - worth keeping all three as comments since each looked
    fixed until tested against more real data:

    1. Concatenating a whole advisory's free text (including OTHER CVEs'
       notes in a multi-CVE advisory, and the document-level disclaimer/
       summary boilerplate) let one CVE's component name leak into the
       recognition check for unrelated CVEs. Fixed by scoping the caller to
       just THIS CVE's own notes (see vulnerability_text()).

    2. Even scoped correctly, bare rapidfuzz `partial_ratio` reproduced the
       spurious-match bug already documented in code/matching/cascade.py
       (FAILURE_LOG.md #3): "red hat" scored 85.7 against one advisory's
       disclaimer text from character-level coincidence alone. Tried
       raising the threshold based on a hand-picked calibration set (100 for
       genuine matches, ~57 for the worst spurious case measured at the
       time) and picked 80 as a clean-looking cutoff.

    3. That threshold then failed on a DIFFERENT real case: "red hat" scored
       85.7 - back above the "fixed" threshold - against the *actual, full*
       Perl CVE description (which contains a shell code snippet with
       special characters), not the shortened version used for hand
       calibration. That's the real lesson: partial_ratio's score for a
       given alias depends on the exact text it's compared against in ways
       that don't generalize from a handful of calibration examples - no
       single threshold reliably separates genuine from spurious matches
       across the full diversity of real advisory text. This concretely
       validates the debrief's original caution about free-text mining
       ("fragile, exactly the semantic-similarity-is-dangerous problem") -
       even a "safer" dictionary-lookup version of it needed a fundamentally
       different technique, not just a better-tuned threshold.

    Fixed by dropping fuzzy scoring for this check entirely and using strict
    word-boundary regex containment instead - deterministic, immune to
    character-level coincidence, at the cost of not catching typos/minor
    spelling variants (an acceptable trade: a missed match here just means
    routing to human review, the safe default, not a false explanation).
    """
    text_norm = normalize(free_text)
    for key, entry in dictionary.items():
        for alias in entry["aliases_seen"]:
            alias_norm = normalize(alias)
            pattern = r"\b" + re.escape(alias_norm) + r"\b"
            if re.search(pattern, text_norm):
                return key
    return None


def main():
    advisories = load_advisories_chronological()
    dictionary: dict[str, dict] = {}
    rows = []

    total_mismatches = 0
    total_matches = 0
    total_no_nvd_data = 0
    recognized_before_seen = 0

    for release_date, advisory_id, doc in advisories:
        advisory_vendor = doc["document"].get("publisher", {}).get("name", "")

        for v in doc.get("vulnerabilities", []):
            cve_id = v.get("cve")
            if not cve_id:
                continue
            nvd_path = NVD_DIR / f"{cve_id}.json"
            if not nvd_path.exists():
                continue  # no NVD record at all
            try:
                json.loads(nvd_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue  # malformed, e.g. CVE-2026-35078 (skip-don't-abort, per known limitations)
            affected_pairs = nvd_affected_for_cve(cve_id)
            if not affected_pairs:
                total_no_nvd_data += 1  # NVD record exists but vendor field is "n/a" throughout
                continue
            nvd_vendors = [vendor for vendor, _ in affected_pairs]

            matches_any = any(is_vendor_match(advisory_vendor, nv) for nv in nvd_vendors)
            if matches_any:
                total_matches += 1
                continue  # not a mismatch, nothing for the dictionary to do here

            total_mismatches += 1

            # Was this mismatch already explainable by the dictionary AS IT
            # STOOD before this advisory was processed (i.e. built from
            # earlier advisories only)? Scoped to this CVE's own notes only -
            # see vulnerability_text()'s docstring for why document-level
            # notes were dropped from this check.
            cve_text = vulnerability_text(v)
            recognized_key = dictionary_recognizes(cve_text, dictionary)
            if recognized_key:
                recognized_before_seen += 1

            rows.append({
                "advisory_id": advisory_id,
                "release_date": release_date,
                "cve": cve_id,
                "advisory_vendor": advisory_vendor,
                # deduped for readability - a CVE can list the same vendor
                # once per affected product variant (e.g. CODESYS appearing
                # 15 times for 15 affected runtime products on one CVE)
                "nvd_vendors": sorted(set(nvd_vendors)),
                "recognized_by_dictionary": recognized_key,
                "dictionary_size_at_time": len(dictionary),
            })

            # Now "resolve" it (simulating a human confirming the true
            # component, which in this prototype is just NVD's own vendor
            # field) and grow the dictionary for future advisories.
            products_by_vendor: dict[str, list[str]] = {}
            for nv, product in affected_pairs:
                products_by_vendor.setdefault(nv, []).append(product)

            for nv, products in products_by_vendor.items():
                key = normalize(nv)
                if key not in dictionary:
                    dictionary[key] = {
                        "aliases_seen": [nv],
                        "first_seen_advisory": advisory_id,
                        "occurrences": [],
                    }
                if nv not in dictionary[key]["aliases_seen"]:
                    dictionary[key]["aliases_seen"].append(nv)
                # See product_family_aliases() docstring: a repeated leading
                # word across this vendor's product variants (e.g.
                # "Windows") is a genuine brand signal advisories are more
                # likely to name than the parent legal-entity vendor name.
                for alias in product_family_aliases(products):
                    if alias not in dictionary[key]["aliases_seen"]:
                        dictionary[key]["aliases_seen"].append(alias)
                dictionary[key]["occurrences"].append({"advisory_id": advisory_id, "cve": cve_id})

    (HERE / "component_knowledge.json").write_text(json.dumps(dictionary, indent=2))
    (HERE / "results_component_kb.json").write_text(json.dumps(rows, indent=2))

    total_checked = total_matches + total_mismatches + total_no_nvd_data
    print(f"{len(advisories)} advisories processed in chronological order")
    print(f"CVE-to-advisory pairs checked (NVD record exists): {total_checked}")
    print(f"  vendor matches:        {total_matches} ({total_matches/total_checked:.1%})")
    print(f"  genuine mismatches:    {total_mismatches} ({total_mismatches/total_checked:.1%})")
    print(f"  no usable NVD vendor:  {total_no_nvd_data} ({total_no_nvd_data/total_checked:.1%})")
    print(f"Recognized by dictionary built from EARLIER advisories only: "
          f"{recognized_before_seen} ({recognized_before_seen/total_mismatches:.1%})")
    print(f"Final dictionary size: {len(dictionary)} components")
    print()
    print("Per-mismatch detail (chronological order):")
    for r in rows:
        tag = f"RECOGNIZED ({r['recognized_by_dictionary']})" if r["recognized_by_dictionary"] else "NOVEL - needs review"
        print(f"  [{r['advisory_id']}] {r['cve']}: {r['advisory_vendor']!r} vs NVD {r['nvd_vendors']} "
              f"-- dict_size_before={r['dictionary_size_at_time']} -- {tag}")


if __name__ == "__main__":
    main()
