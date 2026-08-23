"""
Slice B evaluation: retrieval quality, abstention accuracy, and attribution
on the hand-labelled qa_pairs.json set (roadmap Phase 5 metrics, scaled to
this pilot's 15-pair set per the Kickoff Plan's "10-15, scaled down from
20-30 given the compressed timeline").

Faithfulness here is a keyword-presence heuristic, not a full NLI check —
flagged as such in the printed report. Every question's full context and
raw answer is written to results_qa.json so a human can spot-check the ones
the heuristic is unsure about, same "measured, not guessed, but honestly
scoped" approach as the matching cascade's results_matching.json.
"""
import json
from pathlib import Path

from generate_answer import answer
from retrieval import Retriever

HERE = Path(__file__).resolve().parent
QA_PATH = HERE / "qa_pairs.json"
RESULTS_PATH = HERE / "results_qa.json"


def keyword_hit(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True  # nothing to check (unanswerable pairs)
    low = text.lower()
    return any(kw.lower() in low for kw in keywords)


def main():
    retriever = Retriever()
    qa_pairs = json.loads(QA_PATH.read_text())

    rows = []
    for pair in qa_pairs:
        result = answer(retriever, pair["question"], top_k=5)

        retrieved_ids = [r["advisory_id"] for r in result["retrieved"]]
        if pair["answerable"]:
            rank = (
                retrieved_ids.index(pair["expected_advisory_id"]) + 1
                if pair["expected_advisory_id"] in retrieved_ids
                else None
            )
            retrieval_hit = rank is not None
            abstention_correct = not result["abstained"]
            cited_correct_source = (
                pair["expected_advisory_id"] in result["answer"]
                if not result["abstained"]
                else False
            )
            faithful = (
                keyword_hit(result["answer"], pair["expected_keywords"])
                if not result["abstained"]
                else False
            )
        else:
            rank = None
            retrieval_hit = None
            abstention_correct = result["abstained"]
            cited_correct_source = None
            faithful = None

        rows.append({
            "id": pair["id"],
            "question": pair["question"],
            "answerable": pair["answerable"],
            "expected_advisory_id": pair["expected_advisory_id"],
            "abstained": result["abstained"],
            "abstention_correct": abstention_correct,
            "retrieval_rank": rank,
            "retrieval_hit": retrieval_hit,
            "cited_correct_source": cited_correct_source,
            "faithful_keyword_check": faithful,
            "best_distance": result["best_distance"],
            "answer": result["answer"],
            "retrieved": result["retrieved"],
        })
        print(f"{pair['id']}: abstained={result['abstained']} "
              f"abstention_correct={abstention_correct} "
              f"retrieval_hit={retrieval_hit}")

    RESULTS_PATH.write_text(json.dumps(rows, indent=2))

    n = len(rows)
    answerable_rows = [r for r in rows if r["answerable"]]
    unanswerable_rows = [r for r in rows if not r["answerable"]]

    abstention_acc = sum(r["abstention_correct"] for r in rows) / n
    retrieval_hit_rate = (
        sum(r["retrieval_hit"] for r in answerable_rows) / len(answerable_rows)
        if answerable_rows else float("nan")
    )
    mrr = (
        sum(1 / r["retrieval_rank"] for r in answerable_rows if r["retrieval_rank"])
        / len(answerable_rows)
        if answerable_rows else float("nan")
    )
    attribution_acc = (
        sum(1 for r in answerable_rows if r["cited_correct_source"]) / len(answerable_rows)
        if answerable_rows else float("nan")
    )
    faithfulness = (
        sum(1 for r in answerable_rows if r["faithful_keyword_check"]) / len(answerable_rows)
        if answerable_rows else float("nan")
    )
    false_answer_rate = (
        sum(1 for r in unanswerable_rows if not r["abstention_correct"]) / len(unanswerable_rows)
        if unanswerable_rows else float("nan")
    )

    print()
    print(f"n = {n} ({len(answerable_rows)} answerable, {len(unanswerable_rows)} unanswerable)")
    print(f"Retrieval hit rate @5 (answerable only): {retrieval_hit_rate:.3f}")
    print(f"MRR (answerable only):                   {mrr:.3f}")
    print(f"Abstention accuracy (all 15):             {abstention_acc:.3f}")
    print(f"False-answer rate on unanswerable (should be 0): {false_answer_rate:.3f}")
    print(f"Attribution accuracy (correct source cited):     {attribution_acc:.3f}")
    print(f"Faithfulness (keyword heuristic, answerable only): {faithfulness:.3f}")
    print(f"\nFull results -> {RESULTS_PATH}")


if __name__ == "__main__":
    main()
