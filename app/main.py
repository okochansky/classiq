import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.tasks import router as tasks_router
from app.bootstrap import ensure_group
from app.redis_io.client import close_redis, get_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("classiq.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    r = await get_redis()
    await ensure_group(r)
    logger.info("api.alive")
    try:
        yield
    finally:
        await close_redis()
        logger.info("api.shutdown")


app = FastAPI(title="Classiq QASM Runner", version="0.1.0", lifespan=lifespan)
app.include_router(tasks_router)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "alive"}
