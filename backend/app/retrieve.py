"""Hybrid retrieval: BM25 (+ dense embeddings when CLAIMLENS_DENSE=1), fused with reciprocal rank fusion.
Scoped to the chosen policy plus IRDAI clauses in force on the policy start date."""
import os
import re
from datetime import date

from rank_bm25 import BM25Okapi

from .ingest import Clause

# One query per rule the engine needs, so every topic is covered even on a larger corpus.
TOPICS = [
    "sum insured limit per policy year",
    "room rent limit per day proportionate deduction",
    "co-payment percentage of claim",
    "deductible applies to each claim",
    "waiting period months continuous coverage",
    "pre-existing diseases waiting period",
    "exclusions not covered",
    "sub-limit procedure payable up to",
    "restoration benefit package rate",
    "non-payable items",
]
_tok = lambda s: re.findall(r"[a-z0-9]+", s.lower())
_dense_model = None


def _dense_scores(query: str, clauses: list[Clause]):
    # ponytail: model loaded on first use; fine for a small corpus, add an ANN index (pgvector) when it grows
    global _dense_model
    from sentence_transformers import SentenceTransformer, util
    _dense_model = _dense_model or SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    q = _dense_model.encode(query, convert_to_tensor=True)
    d = _dense_model.encode([c.text for c in clauses], convert_to_tensor=True)
    return util.cos_sim(q, d)[0].tolist()


def _ranks(scores: list[float]) -> list[int]:
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    r = [0] * len(scores)
    for pos, i in enumerate(order):
        r[i] = pos
    return r


def retrieve(corpus: dict[str, Clause], policy_id: str, as_of: date, extra_query: str = "", per_topic: int = 2) -> list[Clause]:
    pool = [c for c in corpus.values() if (c.doc_id == policy_id) or (c.kind == "irdai" and c.effective <= as_of)]
    if not pool:
        return []
    bm25 = BM25Okapi([_tok(c.title + " " + c.text) for c in pool])
    use_dense = os.environ.get("CLAIMLENS_DENSE") == "1"
    picked: dict[str, Clause] = {}
    for topic in TOPICS + ([extra_query] if extra_query else []):
        lex = list(bm25.get_scores(_tok(topic)))
        fused = [1 / (60 + r) for r in _ranks(lex)]
        if use_dense:
            fused = [f + 1 / (60 + r) for f, r in zip(fused, _ranks(_dense_scores(topic, pool)))]
        for i in sorted(range(len(pool)), key=lambda i: -fused[i])[:per_topic]:
            picked[pool[i].id] = pool[i]
    return list(picked.values())
