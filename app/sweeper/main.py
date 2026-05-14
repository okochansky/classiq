import asyncio
import logging
import os
import signal

from redis.asyncio import Redis

from app.bootstrap import GROUP_NAME, STREAM_KEY, ensure_group
from app.processing import process_task
from app.redis_io.client import close_redis, get_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("classiq.sweeper")


def _install_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)


async def sweep_once(
    r: Redis,
    sweeper_id: str,
    idle_ms: int = 60_000,
    batch: int = 10,
) -> int:
    """Reclaim PEL entries idle longer than `idle_ms` to this sweeper via
    XAUTOCLAIM, then process each via the shared `process_task`.
    Returns the number of entries processed in this cycle."""
    cursor = "0-0"
    processed = 0
    while True:
        new_cursor, claimed, _deleted = await r.xautoclaim(
            STREAM_KEY,
            GROUP_NAME,
            sweeper_id,
            min_idle_time=idle_ms,
            start_id=cursor,
            count=batch,
        )
        if not claimed:
            break
        for entry_id, fields in claimed:
            task_id = fields.get("task_id")
            if not task_id:
                logger.warning(
                    "sweep.malformed_entry entry_id=%s acking", entry_id
                )
                await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
                continue
            logger.info(
                "sweep.reclaimed task_id=%s entry_id=%s", task_id, entry_id
            )
            await process_task(r, task_id, entry_id)
            processed += 1
        cursor = new_cursor
        if cursor == "0-0":
            break
    return processed


async def loop() -> None:
    sweeper_id = os.environ.get("SWEEPER_ID", "sweeper-1")
    idle_ms = int(os.environ.get("SWEEPER_IDLE_MS", "10000"))
    interval_s = float(os.environ.get("SWEEPER_INTERVAL_S", "2"))

    stop = asyncio.Event()
    _install_signals(stop)

    r = await get_redis()
    await ensure_group(r)
    logger.info(
        "sweeper.alive sweeper_id=%s idle_ms=%d interval_s=%s",
        sweeper_id, idle_ms, interval_s,
    )

    try:
        while not stop.is_set():
            try:
                count = await sweep_once(r, sweeper_id, idle_ms=idle_ms)
                if count:
                    logger.info("sweep.cycle processed=%d", count)
            except Exception:
                logger.exception("sweeper.loop_error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass
    finally:
        await close_redis()
        logger.info("sweeper.exiting sweeper_id=%s", sweeper_id)


def main() -> None:
    asyncio.run(loop())


if __name__ == "__main__":
    main()
