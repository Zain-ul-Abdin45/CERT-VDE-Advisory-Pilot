"""Flatten a CSAF product_tree into a flat list of matchable product entries.

CSAF nests products as vendor -> product_family -> product_name -> versions,
and separately declares "installed_on" relationships (e.g. firmware installed
on hardware) that create new synthetic product_ids not present in the tree
itself. Both shapes were observed in the real CERT@VDE corpus (VDE-2026-085
is a simple 3-level tree; VDE-2026-005 adds a relationships block). This
flattens both into one list so the matching cascade doesn't need to know
about CSAF's tree structure at all.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field


@dataclass
class ProductEntry:
    advisory_id: str
    vendor: str
    product_name: str          # e.g. "PROCON-WEB SCADA" or "Firmware 3.1 installed on CR3171"
    version_display: str       # human-readable version/range as written in the advisory
    version_kind: str          # "exact" | "range" | "unknown"
    version_spec: str          # raw spec for the range/exact parser (vers: string or plain version)
    cpe: str | None
    model_numbers: list[str] = field(default_factory=list)
    product_id: str = ""
    cve_ids: list[str] = field(default_factory=list)  # filled in later from vulnerabilities[]


def _walk_branches(branches: list[dict], vendor: str, family: str, name: str, out: list[ProductEntry], advisory_id: str):
    # Recursion (updated vendor/family/name context) and leaf-recording (does
    # this branch carry a "product" object) are independent concerns -- a
    # branch always does the former based on its category, and separately
    # does the latter whenever a "product" key is present, regardless of
    # category. Collapsing these into one if/elif chain (the first version
    # of this function) silently dropped any product_name-category branch
    # that had a direct "product" object but no further version branches
    # (e.g. a bare hardware entry like "CR3171" with no version sub-tree).
    for b in branches:
        cat = b.get("category")
        bname = b.get("name", "")
        next_vendor, next_family, next_name = vendor, family, name
        if cat == "vendor":
            next_vendor = bname
        elif cat == "product_family":
            next_family = bname
        elif cat == "product_name":
            next_name = bname

        product = b.get("product")
        if product:
            helper = product.get("product_identification_helper", {})
            is_version = cat in ("product_version", "product_version_range")
            out.append(ProductEntry(
                advisory_id=advisory_id,
                vendor=next_vendor,
                product_name=name or product.get("name", ""),
                version_display=bname if is_version else "",
                version_kind=("range" if cat == "product_version_range" else "exact") if is_version else "unknown",
                version_spec=bname if is_version else "",
                cpe=helper.get("cpe"),
                model_numbers=helper.get("model_numbers", []),
                product_id=product.get("product_id", ""),
            ))

        if b.get("branches"):
            _walk_branches(b["branches"], next_vendor, next_family, next_name, out, advisory_id)


def flatten(csaf: dict, advisory_id: str) -> list[ProductEntry]:
    entries: list[ProductEntry] = []
    tree = csaf.get("product_tree", {})
    _walk_branches(tree.get("branches", []), "", "", "", entries, advisory_id)

    # "installed_on" relationships create combined product_ids (e.g. firmware
    # installed on hardware) that carry their own product_id used by
    # vulnerabilities[].product_status, but no independent product_tree leaf
    # of their own -- surface them as their own matchable entries too.
    by_product_id = {e.product_id: e for e in entries if e.product_id}
    for rel in tree.get("relationships", []):
        if rel.get("category") != "installed_on":
            continue
        fpn = rel.get("full_product_name", {})
        helper = fpn.get("product_identification_helper", {})
        base = by_product_id.get(rel.get("product_reference"))
        entries.append(ProductEntry(
            advisory_id=advisory_id,
            vendor=base.vendor if base else "",
            product_name=fpn.get("name", ""),
            version_display=base.version_display if base else "",
            version_kind=base.version_kind if base else "unknown",
            version_spec=base.version_spec if base else "",
            cpe=helper.get("cpe") or (base.cpe if base else None),
            model_numbers=base.model_numbers if base else [],
            product_id=fpn.get("product_id", ""),
        ))

    # attach which CVEs actually apply to each product_id (product_status.known_affected)
    pid_to_cves: dict[str, list[str]] = {}
    for vuln in csaf.get("vulnerabilities", []):
        cve = vuln.get("cve", "")
        for pid in vuln.get("product_status", {}).get("known_affected", []):
            pid_to_cves.setdefault(pid, []).append(cve)
    for e in entries:
        e.cve_ids = pid_to_cves.get(e.product_id, [])

    return entries


VERS_RE = re.compile(r"vers:[\w./-]+/(.+)")


def parse_version_spec(spec: str):
    """Parse either a 'vers:generic/>=A|<=B' range string or a plain exact
    version into a predicate fn(version_str) -> bool. Falls back to exact
    string equality if the format isn't recognised, rather than raising --
    an unparsed range should show up as a cascade miss, not a crash."""
    m = VERS_RE.match(spec or "")
    if not m:
        target = (spec or "").strip()
        return lambda v: _version_tuple(v) == _version_tuple(target) if target else False

    constraints = m.group(1).split("|")
    parsed = []
    for c in constraints:
        c = c.strip()
        for op in (">=", "<=", ">", "<", "=="):
            if c.startswith(op):
                parsed.append((op, c[len(op):]))
                break

    def predicate(v: str) -> bool:
        vt = _version_tuple(v)
        if vt is None:
            return False
        for op, bound in parsed:
            bt = _version_tuple(bound)
            if bt is None:
                continue
            if op == ">=" and not (vt >= bt):
                return False
            if op == "<=" and not (vt <= bt):
                return False
            if op == ">" and not (vt > bt):
                return False
            if op == "<" and not (vt < bt):
                return False
            if op == "==" and not (vt == bt):
                return False
        return True

    return predicate


def _version_tuple(v: str):
    if not v:
        return None
    parts = re.findall(r"\d+", v)
    if not parts:
        return None
    return tuple(int(p) for p in parts)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "data/advisories/VDE-2026-005/csaf.json"
    csaf = json.load(open(path))
    aid = csaf["document"]["tracking"]["id"]
    for e in flatten(csaf, aid):
        print(e)
