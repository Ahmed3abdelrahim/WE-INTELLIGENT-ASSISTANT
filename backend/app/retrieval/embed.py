"""BAAI/bge-m3 dense+sparse embeddings (SPEC.md section 2)."""
from ..config import config

_model = None
_USE_FP16 = config.EMBED_DEVICE.startswith("cuda")


def get_model():
    global _model
    if _model is None:
        from FlagEmbedding import BGEM3FlagModel

        _model = BGEM3FlagModel(
            str(config.MODELS_ENCODER_DIR / "bge-m3"), use_fp16=_USE_FP16, devices=[config.EMBED_DEVICE]
        )
    return _model


def encode(texts: list[str], max_length: int = 512) -> dict:
    """Returns {"dense": [[float,...], ...], "sparse": [{token_id:int -> weight:float}, ...]}."""
    model = get_model()
    out = model.encode(
        texts,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
        max_length=max_length,
    )
    dense = [vec.tolist() for vec in out["dense_vecs"]]
    sparse = [{int(k): float(v) for k, v in lw.items()} for lw in out["lexical_weights"]]
    return {"dense": dense, "sparse": sparse}


def encode_one(text: str, max_length: int = 512) -> dict:
    out = encode([text], max_length=max_length)
    return {"dense": out["dense"][0], "sparse": out["sparse"][0]}


_reranker = None


def get_reranker():
    global _reranker
    if _reranker is None:
        from FlagEmbedding import FlagReranker

        _reranker = FlagReranker(
            str(config.MODELS_ENCODER_DIR / "bge-reranker-v2-m3"), use_fp16=_USE_FP16, devices=[config.EMBED_DEVICE]
        )
    return _reranker


def rerank(query: str, passages: list[str]) -> list[float]:
    reranker = get_reranker()
    pairs = [[query, p] for p in passages]
    scores = reranker.compute_score(pairs, normalize=True)
    if isinstance(scores, float):
        scores = [scores]
    return list(scores)
