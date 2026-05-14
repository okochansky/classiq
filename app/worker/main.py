import asyncio
import json
import logging
import os
import signal
import socket
from datetime import datetime, timezone

from redis.asyncio import Redis

from app.bootstrap import GROUP_NAME, STREAM_KEY, ensure_group
from app.redis_io.client import close_redis, get_redis
from app.worker.runner import QASMExecutionError, execute_qasm3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("classiq.worker")

_stop: asyncio.Event | None = None


def _install_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _process_one(r: Redis, task_id: str, entry_id: str) -> None:
    state_key = f"state:{task_id}"
    state = await r.hgetall(state_key)

    if not state:
        logger.warning("task.unknown task_id=%s — acking to avoid PEL bloat", task_id)
        await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
        return

    if state.get("status") in ("completed", "failed"):
        logger.info(
            "task.idempotent_ack task_id=%s status=%s", task_id, state.get("status")
        )
        await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
        return

    await r.hset(state_key, mapping={"status": "running", "started_at": _now()})

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
            "task.completed task_id=%s outcomes=%d", task_id, len(counts)
        )
    except QASMExecutionError as exc:
        await r.hset(
            state_key,
            mapping={"status": "failed", "error": str(exc), "completed_at": _now()},
        )
        logger.info("task.failed task_id=%s err=%s", task_id, exc)
    except Exception as exc:
        # Unexpected — record the failure terminally so the GET endpoint can answer
        # truthfully, then ACK. Block C will introduce the sweeper + retry policy.
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


async def run_once(r: Redis, consumer: str, block_ms: int = 2000) -> int:
    resp = await r.xreadgroup(
        GROUP_NAME, consumer, {STREAM_KEY: ">"}, count=1, block=block_ms
    )
    if not resp:
        return 0
    processed = 0
    for _stream, entries in resp:
        for entry_id, fields in entries:
            await _process_one(r, fields["task_id"], entry_id)
            processed += 1
    return processed


async def loop() -> None:
    worker_id = os.environ.get("WORKER_ID", f"worker-{socket.gethostname()}")
    stop = asyncio.Event()
    _install_signals(stop)

    r = await get_redis()
    await ensure_group(r)
    logger.info("worker.alive worker_id=%s", worker_id)

    try:
        while not stop.is_set():
            try:
                await run_once(r, worker_id, block_ms=2000)
            except Exception:
                logger.exception("worker.loop_error")
                await asyncio.sleep(1)
    finally:
        await close_redis()
        logger.info("worker.exiting worker_id=%s", worker_id)


def main() -> None:
    asyncio.run(loop())


if __name__ == "__main__":
    main()
