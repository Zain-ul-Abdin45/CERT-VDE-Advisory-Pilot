"""Run the matching cascade against the synthetic asset inventory and score
it against ground truth: precision/recall per stage and combined, false
positives and false negatives counted separately (per Kickoff Plan Week 3a --
the asymmetry is the number the memo needs, not just an aggregate accuracy).
"""
import json
from cascade import load_all_entries, build_families, match_asset

ADVISORIES_DIR = "../../data/advisories"
INVENTORY_PATH = "../../data/synthetic_asset_inventory.json"


def main():
    entries = load_all_entries(ADVISORIES_DIR)
    families = build_families(entries)
    inventory = json.load(open(INVENTORY_PATH))["assets"]

    rows = []
    for asset in inventory:
        result = match_asset(asset, entries, families)
        gt = asset["ground_truth"]
        should_match = gt["should_match"]
        correct_advisory = (result.advisory_id == gt.get("advisory_id")) if should_match else True

        if should_match and result.matched and correct_advisory:
            outcome = "TP"
        elif should_match and (not result.matched or not correct_advisory):
            outcome = "FN"          # should have matched, didn't (or matched the wrong advisory)
        elif not should_match and result.matched:
            outcome = "FP"          # shouldn't have matched anything, but did
        else:
            outcome = "TN"          # correctly matched nothing

        version_correct = None
        if should_match and outcome == "TP" and gt.get("is_affected") is not None:
            version_correct = (result.is_affected == gt["is_affected"])

        rows.append({
            "asset_id": asset["asset_id"], "kind": gt["kind"], "outcome": outcome,
            "stage": result.stage, "score": result.score,
            "expected_advisory": gt.get("advisory_id"), "matched_advisory": result.advisory_id,
            "expected_affected": gt.get("is_affected"), "predicted_affected": result.is_affected,
            "version_correct": version_correct,
        })

    tp = sum(1 for r in rows if r["outcome"] == "TP")
    fp = sum(1 for r in rows if r["outcome"] == "FP")
    fn = sum(1 for r in rows if r["outcome"] == "FN")
    tn = sum(1 for r in rows if r["outcome"] == "TN")
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")

    print(f"{'asset':6} {'kind':28} {'outcome':4} {'stage':12} {'score':6} {'exp_adv':22} {'got_adv':22} {'ver_ok'}")
    for r in rows:
        print(f"{r['asset_id']:6} {r['kind']:28} {r['outcome']:4} "
              f"{str(r['stage']):12} {str(r['score']):6} "
              f"{str(r['expected_advisory']):22} {str(r['matched_advisory']):22} "
              f"{r['version_correct']}")

    print()
    print(f"TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"Precision={precision:.3f}  Recall={recall:.3f}")

    by_stage = {}
    for r in rows:
        if r["outcome"] == "TP":
            by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + 1
    print(f"TPs by resolving stage: {by_stage}")

    ver_checked = [r for r in rows if r["version_correct"] is not None]
    ver_correct = sum(1 for r in ver_checked if r["version_correct"])
    if ver_checked:
        print(f"Version-range accuracy: {ver_correct}/{len(ver_checked)} "
              f"({ver_correct/len(ver_checked):.1%})")

    fps = [r for r in rows if r["outcome"] == "FP"]
    fns = [r for r in rows if r["outcome"] == "FN"]
    if fps:
        print("\nFalse positives (dangerous -- flagged a match that shouldn't exist):")
        for r in fps:
            print(f"  {r['asset_id']} ({r['kind']}) matched {r['matched_advisory']} via {r['stage']} score={r['score']}")
    if fns:
        print("\nFalse negatives (dangerous -- missed a real match):")
        for r in fns:
            print(f"  {r['asset_id']} ({r['kind']}) expected {r['expected_advisory']}, got {r['matched_advisory']}")

    with open("../../results_matching.json", "w") as f:
        json.dump({"rows": rows, "precision": precision, "recall": recall,
                    "tp": tp, "fp": fp, "fn": fn, "tn": tn, "by_stage": by_stage}, f, indent=2)


if __name__ == "__main__":
    main()
