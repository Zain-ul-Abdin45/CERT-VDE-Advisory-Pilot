"""
Week 4: does answer accuracy depend on which format the same advisory is
read from? Filters the existing chunk store to CSAF-only and HTML-only
subsets (no new ingestion — every chunk already carries a `format` tag from
build_chunks.py) and runs the same answerable questions through each,
using identical retrieval/generation logic (Retriever now accepts a
pre-filtered chunk list, see retrieval.py).

PDF is not part of this comparison — see FAILURE_LOG.md #1, no PDF
advisories exist in the current CERT@VDE corpus.
"""
import json
from pathlib import Path

from generate_answer import answer
from retrieval import Retriever

HERE = Path(__file__).resolve().parent
CHUNKS_PATH = HERE / "chunks.json"
QA_PATH = HERE / "qa_pairs.json"
RESULTS_PATH = HERE / "results_format_comparison.json"


def keyword_hit(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    low = text.lower()
    return any(kw.lower() in low for kw in keywords)


def run_condition(label: str, chunks: list[dict], qa_pairs: list[dict]) -> list[dict]:
    retriever = Retriever(chunks=chunks)
    rows = []
    for pair in qa_pairs:
        result = answer(retriever, pair["question"], top_k=5)
        retrieved_ids = [r["advisory_id"] for r in result["retrieved"]]
        retrieval_hit = pair["expected_advisory_id"] in retrieved_ids
        answered_correctly = (
            not result["abstained"]
            and pair["expected_advisory_id"] in result["answer"]
            and keyword_hit(result["answer"], pair["expected_keywords"])
        )
        rows.append({
            "condition": label,
            "id": pair["id"],
            "question": pair["question"],
            "abstained": result["abstained"],
            "retrieval_hit": retrieval_hit,
            "answered_correctly": answered_correctly,
            "best_distance": result["best_distance"],
            "answer": result["answer"],
        })
        print(f"  [{label}] {pair['id']}: abstained={result['abstained']} "
              f"retrieval_hit={retrieval_hit} answered_correctly={answered_correctly}")
    return rows


def main():
    all_chunks = json.loads(CHUNKS_PATH.read_text())
    qa_pairs = [p for p in json.loads(QA_PATH.read_text()) if p["answerable"]]

    csaf_chunks = [c for c in all_chunks if c["format"] == "csaf"]
    html_chunks = [c for c in all_chunks if c["format"] == "html"]
    print(f"CSAF-only: {len(csaf_chunks)} chunks. HTML-only: {len(html_chunks)} chunks. "
          f"{len(qa_pairs)} answerable questions.\n")

    print("Running CSAF-only condition:")
    csaf_rows = run_condition("csaf", csaf_chunks, qa_pairs)
    print("\nRunning HTML-only condition:")
    html_rows = run_condition("html", html_chunks, qa_pairs)

    all_rows = csaf_rows + html_rows
    RESULTS_PATH.write_text(json.dumps(all_rows, indent=2))

    def summarize(rows, label):
        n = len(rows)
        hit_rate = sum(r["retrieval_hit"] for r in rows) / n
        abstain_rate = sum(r["abstained"] for r in rows) / n
        correct_rate = sum(r["answered_correctly"] for r in rows) / n
        print(f"\n{label}: n={n} retrieval_hit_rate={hit_rate:.3f} "
              f"abstain_rate={abstain_rate:.3f} answer_accuracy={correct_rate:.3f}")
        return {"n": n, "hit_rate": hit_rate, "abstain_rate": abstain_rate, "accuracy": correct_rate}

    csaf_summary = summarize(csaf_rows, "CSAF-only")
    html_summary = summarize(html_rows, "HTML-only")

    disagreements = [
        (c["id"], c["answered_correctly"], h["answered_correctly"])
        for c, h in zip(csaf_rows, html_rows)
        if c["answered_correctly"] != h["answered_correctly"]
    ]
    print(f"\nQuestions where the two formats disagree on correctness: {len(disagreements)}")
    for qid, csaf_ok, html_ok in disagreements:
        print(f"  {qid}: csaf_correct={csaf_ok} html_correct={html_ok}")

    print(f"\nFull results -> {RESULTS_PATH}")


if __name__ == "__main__":
    main()
