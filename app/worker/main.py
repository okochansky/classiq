import asyncio
import os
import signal
import socket

import structlog
from redis.asyncio import Redis

from app.bootstrap import GROUP_NAME, STREAM_KEY, ensure_group
from app.core.logging import configure_logging
from app.observability.server import start_metrics_server
from app.processing import process_task
from app.redis_io.client import close_redis, get_redis

configure_logging("worker")
log = structlog.get_logger("classiq.worker")


def _install_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)


async def run_once(r: Redis, consumer: str, block_ms: int = 2000) -> int:
    """Single XREADGROUP iteration. Returns the number of entries processed.
    Extracted from the loop so tests can drive a single delivery deterministically."""
    # count=1: prefetch-1 caps crash blast radius to one task per worker.
    # block=block_ms (2s default): balances idle-Redis chatter against
    #   SIGTERM responsiveness — the loop only checks `stop` between calls.
    # ">" = "deliver only new entries to me" (vs replaying my own PEL).
    resp = await r.xreadgroup(
        GROUP_NAME, consumer, {STREAM_KEY: ">"}, count=1, block=block_ms
    )
    if not resp:
        return 0
    processed = 0
    for _stream, entries in resp:
        for entry_id, fields in entries:
            await process_task(r, fields["task_id"], entry_id)
            processed += 1
    return processed


async def loop() -> None:
    worker_id = os.environ.get("WORKER_ID", f"worker-{socket.gethostname()}")
    metrics_port = int(os.environ.get("METRICS_PORT", "8001"))
    stop = asyncio.Event()
    _install_signals(stop)

    start_metrics_server(metrics_port)
    structlog.contextvars.bind_contextvars(worker_id=worker_id)

    r = await get_redis()
    await ensure_group(r)
    log.info("worker.alive", metrics_port=metrics_port)

    try:
        while not stop.is_set():
            # Supervisor pattern: a single transient failure (Redis blip,
            # unexpected exception) should not take the worker down.
            # Docker would restart us, but logging + sleep + continue is
            # cheaper than a container restart for a one-off error.
            try:
                await run_once(r, worker_id, block_ms=2000)
            except Exception:
                log.exception("worker.loop_error")
                await asyncio.sleep(1)
    finally:
        await close_redis()
        log.info("worker.exiting")


def main() -> None:
    asyncio.run(loop())


if __name__ == "__main__":
    main()
