"""The matching cascade: exact -> fuzzy -> version-range -> part-number.

Per SRAG Preparation Roadmap.md Phase 4 Step 5 / Kickoff Plan Week 3a,
adapted from the CI pipeline's alias-gate *shape* (README_CI_PIPELINE.md /
enrich.py). Note the CI pipeline's actual technique is substring match
against a hand-curated alias list -- that doesn't transfer here, since an
operator's asset-naming can't be pre-enumerated. This cascade uses real
approximate string matching (rapidfuzz) for that reason, not a straight
port of the CI pipeline's code.

Identity matching (exact/fuzzy/part-number) finds the PRODUCT FAMILY an
asset belongs to; version-range logic then determines whether the asset's
specific version is inside that family's affected range. This mirrors how
CSAF actually encodes products (a single product_name can have many version
branches, each independently marked affected/fixed) rather than treating
"version-range" as a fourth independent identity-matching signal.
"""
from __future__ import annotations
import json
import re
import glob
from dataclasses import dataclass
from rapidfuzz import fuzz

import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from flatten_csaf import flatten, parse_version_spec, ProductEntry

FUZZY_THRESHOLD = 82       # WRatio floor
TOKEN_SET_FLOOR = 60       # guards against WRatio's partial-ratio component scoring
                            # a short query as a spurious high match against an
                            # unrelated but much longer product string (observed:
                            # a 5-word query scored 85.5 WRatio against an
                            # unrelated 12-word product name purely from character
                            # overlap; token_set_ratio on the same pair was 35.6,
                            # vs 78-100 for every genuine match in this corpus --
                            # a clear, well-separated gate, not an arbitrary tune)


def normalize(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)   # drop punctuation so "TBEN-L4-SE-M2" ~ "TBEN L4 SE M2"
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_all_entries(advisories_dir: str) -> list[ProductEntry]:
    entries: list[ProductEntry] = []
    for f in sorted(glob.glob(f"{advisories_dir}/*/csaf.json")):
        try:
            csaf = json.load(open(f))
        except Exception:
            continue
        aid = csaf["document"]["tracking"]["id"]
        entries.extend(flatten(csaf, aid))
    return entries


def build_families(entries: list[ProductEntry]) -> dict[tuple, list[ProductEntry]]:
    """Group version-branch entries into product families keyed by
    (advisory_id, normalized vendor, normalized product_name)."""
    families: dict[tuple, list[ProductEntry]] = {}
    for e in entries:
        key = (e.advisory_id, normalize(e.vendor), normalize(e.product_name))
        families.setdefault(key, []).append(e)
    return families


@dataclass
class MatchResult:
    asset_id: str
    matched: bool
    stage: str | None          # "exact" | "fuzzy" | "part_number" | None
    advisory_id: str | None
    family_key: tuple | None
    score: float | None
    is_affected: bool | None
    matched_entry_version: str | None


def _asset_blob(asset: dict) -> str:
    return normalize(f"{asset['vendor']} {asset['product']}")


def exact_stage(asset: dict, families: dict) -> tuple | None:
    blob = _asset_blob(asset)
    for key in families:
        family_blob = normalize(f"{key[1]} {key[2]}")
        if blob == family_blob:
            return key
    return None


def fuzzy_stage(asset: dict, families: dict) -> tuple[tuple, float] | None:
    """WRatio (rapidfuzz's general-purpose composite metric) rather than a
    single ratio type -- verified against real cases in this corpus to
    correctly resolve genuine spacing/word-order variance (e.g. "VariTron
    300" vs "variTRON300") that token_sort_ratio alone missed, without
    weakening detection of the dangerous near-miss case (PROCON-WEB SCADA
    vs the fictional "...XT" variant still scores ~95, correctly surfacing
    as a false positive risk, not something this metric choice papers over)."""
    blob = _asset_blob(asset)
    best_key, best_score = None, 0.0
    for key in families:
        family_blob = normalize(f"{key[1]} {key[2]}")
        score = fuzz.WRatio(blob, family_blob)
        if score > best_score and fuzz.token_set_ratio(blob, family_blob) >= TOKEN_SET_FLOOR:
            best_key, best_score = key, score
    if best_key is not None and best_score >= FUZZY_THRESHOLD:
        return best_key, best_score
    return None


def part_number_stage(asset: dict, entries: list[ProductEntry], families: dict) -> tuple | None:
    part_no = (asset.get("part_no") or "").strip()
    if not part_no:
        return None
    norm_part = normalize(part_no)
    for e in entries:
        for mn in e.model_numbers:
            if normalize(mn) == norm_part or norm_part in normalize(mn):
                return (e.advisory_id, normalize(e.vendor), normalize(e.product_name))
        # some advisories put the part number directly in the product name
        # (Balluff, Pilz observed in the real corpus) rather than model_numbers
        if norm_part and norm_part in normalize(e.product_name):
            return (e.advisory_id, normalize(e.vendor), normalize(e.product_name))
    return None


def version_check(asset: dict, family_entries: list[ProductEntry]) -> tuple[bool | None, str | None]:
    """Among the matched family's version branches, does the asset's version
    fall inside an affected range/exact-match, a fixed one, or neither
    (unknown -- asset has no comparable version info, e.g. hardware-only)."""
    version = asset.get("version")
    if not version:
        return None, None
    affected_entry = None
    fixed_entry = None
    for e in family_entries:
        if not e.version_spec:
            continue
        predicate = parse_version_spec(e.version_spec)
        if predicate(version):
            if e.cve_ids:
                affected_entry = e
            else:
                fixed_entry = e
    if affected_entry:
        return True, affected_entry.version_display
    if fixed_entry:
        return False, fixed_entry.version_display
    return None, None


def match_asset(asset: dict, entries: list[ProductEntry], families: dict) -> MatchResult:
    # Order: exact (unambiguous) -> part-number (near-certain when present,
    # per the debrief's own domain reasoning -- "better than any fuzzy
    # text") -> fuzzy (fallback). Checking part-number before fuzzy matters:
    # a mediocre-but-threshold-passing fuzzy score would otherwise win by
    # running first and short-circuiting a correct, higher-confidence
    # part-number match that was available on the same asset.
    key = exact_stage(asset, families)
    stage, score = ("exact", 100.0) if key else (None, None)

    if key is None:
        pn_key = part_number_stage(asset, entries, families)
        if pn_key:
            key, stage, score = pn_key, "part_number", None

    if key is None:
        fz = fuzzy_stage(asset, families)
        if fz:
            key, score = fz
            stage = "fuzzy"

    if key is None:
        return MatchResult(asset["asset_id"], False, None, None, None, None, None, None)

    is_affected, matched_version = version_check(asset, families[key])
    return MatchResult(
        asset_id=asset["asset_id"], matched=True, stage=stage,
        advisory_id=key[0], family_key=key, score=score,
        is_affected=is_affected, matched_entry_version=matched_version,
    )
