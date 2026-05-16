import json
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.bootstrap import STREAM_KEY
from app.observability.metrics import tasks_total
from app.redis_io.client import get_redis

log = structlog.get_logger("classiq.api")
router = APIRouter()

STATE_KEY_PREFIX = "state:"


def _state_key(task_id: str) -> str:
    return f"{STATE_KEY_PREFIX}{task_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 1 MiB cap on the QASM payload — defends the API against pathological
# bodies, and any sensible production circuit serializes well below this.
QC_MAX_LEN = 1_048_576


class TaskCreate(BaseModel):
    qc: str = Field(
        min_length=1,
        max_length=QC_MAX_LEN,
        description="Serialized QASM3 circuit (up to 1 MiB).",
    )
    shots: int = Field(default=1024, ge=1, le=100_000)


class TaskCreated(BaseModel):
    task_id: str
    message: str = "Task submitted successfully."


# 202 (not 200/201): semantically "I've accepted your work, check back later" —
# the response is a handle, not the result.
@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED, response_model=TaskCreated)
async def submit_task(body: TaskCreate, r: Redis = Depends(get_redis)) -> TaskCreated:
    task_id = str(uuid.uuid4())
    state_key = _state_key(task_id)

    # Write state BEFORE enqueueing. Reverse order would let a worker pick up
    # the entry before the state hash exists, leading to "task.unknown" ACKs.
    await r.hset(
        state_key,
        mapping={
            "status": "pending",
            "qc": body.qc,
            "shots": str(body.shots),
            "created_at": _now(),
        },
    )

    # maxlen=100_000 + approximate=True: bound stream growth, trim in cheap
    # batches. Past 100k unprocessed entries, oldest are evicted — the system
    # is far past healthy at that point anyway.
    try:
        await r.xadd(STREAM_KEY, {"task_id": task_id}, maxlen=100_000, approximate=True)
    # If XADD fails after HSET succeeded we have a phantom: state exists, no
    # queue entry, no worker will ever pick it up. Mark it failed eagerly so
    # GET /tasks/{id} reflects reality instead of "pending forever".
    except RedisError as exc:
        log.error("task.enqueue_failed", task_id=task_id, err=str(exc))
        await r.hset(
            state_key,
            mapping={"status": "failed", "error": f"enqueue_failed: {exc}"},
        )
        tasks_total.labels(event="failed").inc()
        raise HTTPException(status_code=503, detail="Failed to enqueue task.") from exc

    tasks_total.labels(event="accepted").inc()
    log.info(
        "task.accepted",
        task_id=task_id,
        qc_len=len(body.qc),
        shots=body.shots,
    )
    return TaskCreated(task_id=task_id)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, r: Redis = Depends(get_redis)) -> dict:
    state = await r.hgetall(_state_key(task_id))
    if not state:
        return {"status": "error", "message": "Task not found."}

    status_value = state.get("status", "pending")
    if status_value == "completed":
        return {"status": "completed", "result": json.loads(state["result"])}
    if status_value == "failed":
        return {"status": "error", "message": state.get("error", "Task failed.")}
    # "running" is an internal state — externally it's still "pending" from
    # the client's perspective (the result isn't available yet).
    return {"status": "pending", "message": "Task is still in progress."}
