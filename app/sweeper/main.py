import asyncio
import os
import signal

import structlog
from redis.asyncio import Redis

from app.bootstrap import GROUP_NAME, STREAM_KEY, ensure_group
from app.core.logging import configure_logging
from app.observability.metrics import stream_length, stream_pending, tasks_total
from app.observability.server import start_metrics_server
from app.processing import process_task
from app.redis_io.client import close_redis, get_redis

configure_logging("sweeper")
log = structlog.get_logger("classiq.sweeper")


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
                log.warning("sweep.malformed_entry", entry_id=entry_id)
                await r.xack(STREAM_KEY, GROUP_NAME, entry_id)
                continue
            log.info("sweep.reclaimed", task_id=task_id, entry_id=entry_id)
            tasks_total.labels(event="reclaimed").inc()
            await process_task(r, task_id, entry_id)
            processed += 1
        cursor = new_cursor
        if cursor == "0-0":
            break
    return processed


async def _update_gauges(r: Redis) -> None:
    try:
        pending = await r.xpending(STREAM_KEY, GROUP_NAME)
        stream_pending.set(pending.get("pending", 0))
    except Exception:
        log.exception("sweep.pending_probe_failed")
    try:
        stream_length.set(await r.xlen(STREAM_KEY))
    except Exception:
        log.exception("sweep.xlen_failed")


async def loop() -> None:
    sweeper_id = os.environ.get("SWEEPER_ID", "sweeper-1")
    idle_ms = int(os.environ.get("SWEEPER_IDLE_MS", "10000"))
    interval_s = float(os.environ.get("SWEEPER_INTERVAL_S", "2"))
    metrics_port = int(os.environ.get("METRICS_PORT", "8001"))

    stop = asyncio.Event()
    _install_signals(stop)

    start_metrics_server(metrics_port)
    structlog.contextvars.bind_contextvars(sweeper_id=sweeper_id)

    r = await get_redis()
    await ensure_group(r)
    log.info(
        "sweeper.alive",
        idle_ms=idle_ms,
        interval_s=interval_s,
        metrics_port=metrics_port,
    )

    try:
        while not stop.is_set():
            await _update_gauges(r)
            try:
                count = await sweep_once(r, sweeper_id, idle_ms=idle_ms)
                if count:
                    log.info("sweep.cycle", processed=count)
            except Exception:
                log.exception("sweeper.loop_error")
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass
    finally:
        await close_redis()
        log.info("sweeper.exiting")


def main() -> None:
    asyncio.run(loop())


if __name__ == "__main__":
    main()
