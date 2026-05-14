import asyncio
import logging
import os
import signal
import socket

from redis.asyncio import Redis

from app.bootstrap import GROUP_NAME, STREAM_KEY, ensure_group
from app.processing import process_task
from app.redis_io.client import close_redis, get_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("classiq.worker")


def _install_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)


async def run_once(r: Redis, consumer: str, block_ms: int = 2000) -> int:
    """Single XREADGROUP iteration. Returns the number of entries processed.
    Extracted from the loop so tests can drive a single delivery deterministically."""
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
