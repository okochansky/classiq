import logging

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

STREAM_KEY = "tasks:stream"
GROUP_NAME = "workers"

logger = logging.getLogger("classiq.bootstrap")


async def ensure_group(client: aioredis.Redis) -> None:
    try:
        await client.xgroup_create(STREAM_KEY, GROUP_NAME, id="$", mkstream=True)
        logger.info("stream.group_created stream=%s group=%s", STREAM_KEY, GROUP_NAME)
    except ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            return
        raise
