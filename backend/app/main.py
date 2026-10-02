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


@app.on_event("startup")
async def on_startup():
    await init_db()
    logger.info("backend startup complete")
