import structlog
import redis.asyncio as aioredis
from redis.exceptions import ResponseError

STREAM_KEY = "tasks:stream"
GROUP_NAME = "workers"

log = structlog.get_logger("classiq.bootstrap")


async def ensure_group(client: aioredis.Redis) -> None:
    try:
        await client.xgroup_create(STREAM_KEY, GROUP_NAME, id="$", mkstream=True)
        log.info("stream.group_created", stream=STREAM_KEY, group=GROUP_NAME)
    except ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            return
        raise
