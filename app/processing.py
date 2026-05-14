import json
import logging
from datetime import datetime, timezone

from redis.asyncio import Redis

from app.bootstrap import GROUP_NAME, STREAM_KEY
from app.worker.runner import QASMExecutionError, execute_qasm3

logger = logging.getLogger("classiq.processing")

MAX_ATTEMPTS_DEFAULT = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def process_task(
    r: Redis,
    task_id: str,
    entry_id: str,
    max_attempts: int = MAX_ATTEMPTS_DEFAULT,
) -> None:
    """Process a single delivery of a task. Idempotent: a re-delivery of a
    task already in a terminal state is XACK'd without re-execution.
    Per-task attempt counter caps retries (max_attempts) before the task is
    marked failed."""
    state_key = f"state:{task_id}"
    state = await r.hgetall(state_key)

    if not state:
        logger.warning("task.unknown task_id=%s acking", task_id)
        await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
        return

    status_value = state.get("status")
    if status_value in ("completed", "failed"):
        logger.info(
            "task.idempotent_ack task_id=%s status=%s", task_id, status_value
        )
        await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
        return

    attempts = int(await r.hincrby(state_key, "attempts", 1))
    if attempts > max_attempts:
        await r.hset(
            state_key,
            mapping={
                "status": "failed",
                "error": f"max_retries_exceeded after {attempts - 1} attempts",
                "completed_at": _now(),
            },
        )
        logger.warning(
            "task.max_retries task_id=%s attempts=%d", task_id, attempts
        )
        await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
        return

    await r.hset(
        state_key, mapping={"status": "running", "started_at": _now()}
    )

    try:
        counts = execute_qasm3(state["qc"], int(state.get("shots", 1024)))
        await r.hset(
            state_key,
            mapping={
                "status": "completed",
                "result": json.dumps(counts),
                "completed_at": _now(),
            },
        )
        logger.info(
            "task.completed task_id=%s attempts=%d outcomes=%d",
            task_id, attempts, len(counts),
        )
    except QASMExecutionError as exc:
        await r.hset(
            state_key,
            mapping={
                "status": "failed",
                "error": str(exc),
                "completed_at": _now(),
            },
        )
        logger.info("task.failed task_id=%s err=%s", task_id, exc)
    except Exception as exc:
        # Unexpected: mark terminally failed so the GET endpoint can answer
        # truthfully. Acked in finally; sweeper won't redeliver. Transient
        # vs terminal classification is a future improvement.
        await r.hset(
            state_key,
            mapping={
                "status": "failed",
                "error": f"unexpected: {exc}",
                "completed_at": _now(),
            },
        )
        logger.exception("task.unexpected_error task_id=%s", task_id)
    finally:
        await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
