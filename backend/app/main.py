import asyncio
import logging

from fastapi import FastAPI

from .api import router
from .store import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("we-assistant")

app = FastAPI(title="WE Assistant API", version="0.1.0")
app.include_router(router, prefix="/api/v1")


@app.get("/health")
async def root_health():
    from .api import health as health_impl

    return await health_impl()


def _warm_models():
    """Load the embedder (and reranker, if enabled) now instead of on the first question —
    otherwise the first user after a restart waits ~12s on the GPU box for lazy loading."""
    from .config import config
    from .retrieval.embed import encode_one, get_reranker, rerank

    try:
        encode_one("warm up")
        if config.RERANKER_ENABLED:
            get_reranker()
            rerank("warm up", ["warm up"])
        logger.info("retrieval models loaded")
    except Exception as e:  # noqa: BLE001 — a failed warm-up just means lazy loading later
        logger.warning("model warm-up failed (will load on first request): %s", e)


@app.on_event("startup")
async def on_startup():
    await init_db()
    asyncio.get_running_loop().run_in_executor(None, _warm_models)
    logger.info("backend startup complete")
