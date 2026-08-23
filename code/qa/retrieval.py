"""
Hybrid retrieval over the advisory chunk store, adapted from the RAG project's
pattern (README_RAG.md in the inIT planning folder): pgvector cosine search,
BM25 keyword scoring over the same candidate pool, Reciprocal Rank Fusion.

No Postgres here, deliberately — same "lean scripts over full stack" call
already made for the matching cascade in code/matching/. Embeddings are cached
to disk since the chunk set is static; re-run build_embeddings() if chunks.json
changes.
"""
import json
from pathlib import Path

import requests
from rank_bm25 import BM25Okapi

OLLAMA_HOST = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"

HERE = Path(__file__).resolve().parent
CHUNKS_PATH = HERE / "chunks.json"
EMBEDDINGS_PATH = HERE / "embeddings.json"

# NOT the RAG project's default (0.7) — that value doesn't transfer to this
# corpus. Advisory chunks are short, structured, single-topic notes rather
# than long generic-PDF paragraphs, so even off-topic queries ("what is the
# capital of France?") land inside 0.7 by sharing enough embedding-space
# generality (best_dist ~0.56-0.60 across 6 probe queries). Genuine advisory
# questions landed at 0.16-0.36 on the same probe set. 0.45 sits in the
# ~0.20-wide gap between the two clusters. Cosine distance in [0, 2]; chunks
# whose best-scoring representation is >= this are treated as off-topic /
# insufficient evidence.
SEARCH_THRESHOLD = 0.45
CANDIDATE_MULTIPLIER = 4  # fetch TOP_K * this many candidates before fusion


def _tokenize(text: str) -> list[str]:
    return text.lower().split()


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 2.0
    cosine_sim = dot / (norm_a * norm_b)
    return 1 - cosine_sim


def embed(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def build_embeddings():
    chunks = json.loads(CHUNKS_PATH.read_text())
    embeddings = {}
    for i, c in enumerate(chunks):
        embeddings[c["id"]] = embed(c["text"])
        if (i + 1) % 50 == 0:
            print(f"  embedded {i + 1}/{len(chunks)}")
    EMBEDDINGS_PATH.write_text(json.dumps(embeddings))
    print(f"embedded {len(chunks)} chunks -> {EMBEDDINGS_PATH}")


class Retriever:
    def __init__(self):
        self.chunks = json.loads(CHUNKS_PATH.read_text())
        self.by_id = {c["id"]: c for c in self.chunks}
        self.embeddings = json.loads(EMBEDDINGS_PATH.read_text())
        self.bm25 = BM25Okapi([_tokenize(c["text"]) for c in self.chunks])

    def search(self, query: str, top_k: int = 5):
        """Hybrid search. Returns (results, best_distance, abstained).

        results: list of {chunk, cosine_distance, bm25_score, rrf_rank}
        abstained: True if best_distance >= SEARCH_THRESHOLD (no chunk survives)
        """
        query_emb = embed(query)

        # Cosine distance over the whole store, take the candidate pool.
        distances = [
            (i, _cosine_distance(query_emb, self.embeddings[c["id"]]))
            for i, c in enumerate(self.chunks)
        ]
        distances.sort(key=lambda x: x[1])
        candidate_n = min(top_k * CANDIDATE_MULTIPLIER, len(distances))
        candidates = distances[:candidate_n]
        best_distance = candidates[0][1] if candidates else 2.0

        if best_distance >= SEARCH_THRESHOLD:
            return [], best_distance, True

        candidate_idx = [i for i, _ in candidates]
        cosine_rank = {i: rank for rank, (i, _) in enumerate(candidates)}

        # BM25 scored over the same candidate pool.
        query_tokens = _tokenize(query)
        bm25_scores_all = self.bm25.get_scores(query_tokens)
        bm25_ranked = sorted(candidate_idx, key=lambda i: -bm25_scores_all[i])
        bm25_rank = {i: rank for rank, i in enumerate(bm25_ranked)}

        # Reciprocal Rank Fusion.
        k_rrf = 60
        rrf_scores = {}
        for i in candidate_idx:
            rrf_scores[i] = (
                1 / (k_rrf + cosine_rank[i] + 1) + 1 / (k_rrf + bm25_rank[i] + 1)
            )
        fused = sorted(candidate_idx, key=lambda i: -rrf_scores[i])[:top_k]

        dist_by_idx = dict(candidates)
        results = [
            {
                "chunk": self.chunks[i],
                "cosine_distance": dist_by_idx[i],
                "bm25_score": bm25_scores_all[i],
                "rrf_score": rrf_scores[i],
            }
            for i in fused
        ]
        return results, best_distance, False


if __name__ == "__main__":
    if not EMBEDDINGS_PATH.exists():
        print("Building embeddings (one-time, cached to embeddings.json)...")
        build_embeddings()
    else:
        print(f"{EMBEDDINGS_PATH} already exists, skipping. Delete it to rebuild.")
