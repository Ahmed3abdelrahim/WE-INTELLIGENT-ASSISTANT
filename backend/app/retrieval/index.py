"""Qdrant collection `we_chunks`: named vectors dense+sparse, payload indexes (SPEC.md section 5)."""
import uuid

from qdrant_client import QdrantClient, models

from ..config import config

_client = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=config.QDRANT_URL)
    return _client


def ensure_collection():
    client = get_client()
    if client.collection_exists(config.QDRANT_COLLECTION):
        return
    client.create_collection(
        collection_name=config.QDRANT_COLLECTION,
        vectors_config={
            "dense": models.VectorParams(size=1024, distance=models.Distance.COSINE),
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(),
        },
    )
    for field in ("source_type", "session_id", "doc_id"):
        client.create_payload_index(
            collection_name=config.QDRANT_COLLECTION,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )


def upsert_chunks(chunks: list[dict], dense_vecs: list[list[float]], sparse_vecs: list[dict[int, float]]):
    """chunks: list of payload dicts (SPEC.md section 5 fields, must include 'chunk_id')."""
    ensure_collection()
    client = get_client()
    points = []
    for chunk, dense, sparse in zip(chunks, dense_vecs, sparse_vecs, strict=True):
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk["chunk_id"]))
        points.append(
            models.PointStruct(
                id=point_id,
                vector={
                    "dense": dense,
                    "sparse": models.SparseVector(
                        indices=list(sparse.keys()), values=list(sparse.values())
                    ),
                },
                payload=chunk,
            )
        )
    client.upsert(collection_name=config.QDRANT_COLLECTION, points=points, wait=True)
    return len(points)


def delete_by_doc_id(doc_id: str):
    client = get_client()
    client.delete(
        collection_name=config.QDRANT_COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
            )
        ),
    )
