import json
import time
from datetime import datetime, timezone

import structlog
from redis.asyncio import Redis

from app.bootstrap import GROUP_NAME, STREAM_KEY
from app.observability.metrics import qubit_bucket, task_duration_seconds, tasks_total
from app.worker.runner import QASMExecutionError, execute_qasm3

log = structlog.get_logger("classiq.processing")

MAX_ATTEMPTS_DEFAULT = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def process_task(
    r: Redis,
    task_id: str,
    entry_id: str,
    max_attempts: int = MAX_ATTEMPTS_DEFAULT,
) -> None:
    """Process a single delivery of a task. Idempotent on terminal state.
    Per-task `attempts` counter caps retries at `max_attempts`."""
    # Thread task_id / entry_id through every log line in this coroutine.
    # Unbind in the outer finally so the next task doesn't inherit them.
    structlog.contextvars.bind_contextvars(task_id=task_id, entry_id=entry_id)
    try:
        state_key = f"state:{task_id}"
        state = await r.hgetall(state_key)

        # Stream entry references a task whose state row was deleted
        # (manual cleanup, schema bug). ACK to drop the orphan; nothing to retry.
        if not state:
            log.warning("task.unknown")
            tasks_total.labels(event="unknown_ack").inc()
            await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
            return

        # Idempotency: a re-delivery (sweeper reclaim) of a task whose worker
        # died AFTER writing the terminal HSET but BEFORE XACK. Re-executing
        # would overwrite a correct, already-durable result. Just ACK.
        status_value = state.get("status")
        if status_value in ("completed", "failed"):
            log.info("task.idempotent_ack", status=status_value)
            tasks_total.labels(event="idempotent_ack").inc()
            await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
            return

        # HINCRBY is atomic — two sweepers racing to reclaim the same entry
        # cannot lose a count via read-modify-write.
        attempts = int(await r.hincrby(state_key, "attempts", 1))
        # Poison-message cap: a task whose worker keeps crashing on it
        # transitions to terminal `failed` instead of looping forever.
        if attempts > max_attempts:
            await r.hset(
                state_key,
                mapping={
                    "status": "failed",
                    "error": f"max_retries_exceeded after {attempts - 1} attempts",
                    "completed_at": _now(),
                },
            )
            log.warning("task.max_retries", attempts=attempts)
            tasks_total.labels(event="max_retries").inc()
            await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
            return

        await r.hset(
            state_key, mapping={"status": "running", "started_at": _now()}
        )
        tasks_total.labels(event="started").inc()
        log.info("task.started", attempts=attempts)

        bucket = qubit_bucket(state["qc"])
        start = time.monotonic()
        try:
            counts = execute_qasm3(state["qc"], int(state.get("shots", 1024)))
            elapsed = time.monotonic() - start
            task_duration_seconds.labels(qubit_bucket=bucket).observe(elapsed)

            await r.hset(
                state_key,
                mapping={
                    "status": "completed",
                    "result": json.dumps(counts),
                    "completed_at": _now(),
                },
            )
            tasks_total.labels(event="completed").inc()
            log.info(
                "task.completed",
                attempts=attempts,
                outcomes=len(counts),
                duration_s=round(elapsed, 4),
                qubit_bucket=bucket,
            )
        # Client-input error (malformed QASM, unsupported gate). Expected
        # in normal operation — log at INFO so alerts on ERROR-rate stay
        # actionable for real bugs.
        except QASMExecutionError as exc:
            await r.hset(
                state_key,
                mapping={
                    "status": "failed",
                    "error": str(exc),
                    "completed_at": _now(),
                },
            )
            tasks_total.labels(event="failed").inc()
            log.info("task.failed", err=str(exc))
        # Anything we didn't anticipate — bug, library upgrade, OOM.
        # log.exception attaches the stack trace to the JSON record.
        except Exception as exc:
            await r.hset(
                state_key,
                mapping={
                    "status": "failed",
                    "error": f"unexpected: {exc}",
                    "completed_at": _now(),
                },
            )
            tasks_total.labels(event="failed").inc()
            log.exception("task.unexpected_error")
        finally:
            # ACK on every path: failure is durable in the state hash and
            # is not a retry signal. The stream entry's job is done.
            await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
    finally:
        structlog.contextvars.unbind_contextvars("task_id", "entry_id")
