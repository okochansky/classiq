import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.exceptions import RedisError

from app.api.tasks import router as tasks_router
from app.bootstrap import ensure_group
from app.core.logging import configure_logging
from app.observability.metrics import http_request_duration_seconds
from app.redis_io.client import close_redis, get_redis

configure_logging("api")
log = structlog.get_logger("classiq.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    r = await get_redis()
    await ensure_group(r)
    log.info("api.alive")
    try:
        yield
    finally:
        await close_redis()
        log.info("api.shutdown")


app = FastAPI(title="Classiq QASM Runner", version="0.1.0", lifespan=lifespan)
app.include_router(tasks_router)


@app.exception_handler(RedisError)
async def _redis_unavailable(request: Request, exc: RedisError) -> JSONResponse:
    """Any uncaught Redis error becomes a spec-shaped 503 instead of a 500
    leaking a traceback. Route handlers that need finer-grained recovery
    (e.g. mark-state-failed-then-503) still catch RedisError locally."""
    log.error("redis.unavailable", path=str(request.url.path), err=str(exc))
    return JSONResponse(
        status_code=503,
        content={"status": "error", "message": "Backend unavailable. Please retry."},
    )


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - start
    # Use the route template (`/tasks/{task_id}`) when available so the
    # histogram doesn't explode into one series per task_id.
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    http_request_duration_seconds.labels(
        method=request.method,
        route=path,
        status=str(response.status_code),
    ).observe(elapsed)
    return response


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "alive"}


@app.get("/readyz")
async def readyz() -> Response:
    """Returns 200 only when Redis is reachable AND the consumer group
    exists — i.e. the API is ready to enqueue tasks that workers can claim."""
    try:
        r = await get_redis()
        await r.ping()
        groups = await r.xinfo_groups("tasks:stream")
        names = {g.get("name") for g in groups}
        if "workers" not in names:
            return Response(content='{"status":"degraded","reason":"consumer_group_missing"}',
                            status_code=503, media_type="application/json")
        return Response(content='{"status":"ready"}', status_code=200,
                        media_type="application/json")
    except Exception as exc:
        log.warning("readyz.fail", err=str(exc))
        return Response(content=f'{{"status":"unready","reason":"{type(exc).__name__}"}}',
                        status_code=503, media_type="application/json")


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
