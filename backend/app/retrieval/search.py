"""Hybrid (dense+sparse, RRF-fused) retrieval over `we_chunks` (SPEC.md section 6)."""
from qdrant_client import models

from ..config import config
from .embed import encode_one, rerank
from .index import get_client


def build_filter(session_id: str, doc_ids: list[str] | None) -> models.Filter:
    should = [
        models.FieldCondition(key="source_type", match=models.MatchValue(value="official")),
    ]
    if doc_ids:
        should.append(
            models.Filter(
                must=[
                    models.FieldCondition(key="session_id", match=models.MatchValue(value=session_id)),
                    models.FieldCondition(key="doc_id", match=models.MatchAny(any=doc_ids)),
                ]
            )
        )
    return models.Filter(should=should)


def hybrid_search(
    query: str,
    session_id: str,
    doc_ids: list[str] | None = None,
    dense_top_k: int | None = None,
    sparse_top_k: int | None = None,
    final_top_k: int | None = None,
    use_reranker: bool | None = None,
) -> list[dict]:
    dense_top_k = dense_top_k or config.DENSE_TOP_K
    sparse_top_k = sparse_top_k or config.SPARSE_TOP_K
    final_top_k = final_top_k or config.FINAL_TOP_K
    use_reranker = config.RERANKER_ENABLED if use_reranker is None else use_reranker

    qvec = encode_one(query)
    qfilter = build_filter(session_id, doc_ids)
    client = get_client()

    candidate_limit = config.RERANK_CANDIDATE_POOL if use_reranker else final_top_k

    result = client.query_points(
        collection_name=config.QDRANT_COLLECTION,
        prefetch=[
            models.Prefetch(
                query=qvec["dense"],
                using="dense",
                filter=qfilter,
                limit=dense_top_k,
            ),
            models.Prefetch(
                query=models.SparseVector(
                    indices=list(qvec["sparse"].keys()), values=list(qvec["sparse"].values())
                ),
                using="sparse",
                filter=qfilter,
                limit=sparse_top_k,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=candidate_limit,
        with_payload=True,
    )

    hits = [{"score": p.score, **p.payload} for p in result.points]

    if use_reranker and hits:
        passages = [h["text"] for h in hits]
        rerank_scores = rerank(query, passages)
        for h, s in zip(hits, rerank_scores, strict=True):
            h["rerank_score"] = s
        hits.sort(key=lambda h: h["rerank_score"], reverse=True)
        hits = hits[:final_top_k]
    else:
        hits = hits[:final_top_k]

    return hits
