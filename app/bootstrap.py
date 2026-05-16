import structlog
import redis.asyncio as aioredis
from redis.exceptions import ResponseError

STREAM_KEY = "tasks:stream"
GROUP_NAME = "workers"

log = structlog.get_logger("classiq.bootstrap")


async def ensure_group(client: aioredis.Redis) -> None:
    try:
        # id="$": start delivering from the next entry, not the beginning of
        #   the stream — fresh deploys shouldn't replay historical tasks.
        # mkstream=True: atomically create the stream if missing — solves
        #   the chicken-and-egg between API (producer) and worker (consumer)
        #   on first start.
        await client.xgroup_create(STREAM_KEY, GROUP_NAME, id="$", mkstream=True)
        log.info("stream.group_created", stream=STREAM_KEY, group=GROUP_NAME)
    except ResponseError as exc:
        # Expected steady state: API, worker, and sweeper all call this on
        # startup. Whichever runs first creates the group; the others get
        # BUSYGROUP and proceed. Idempotency by trying-then-swallowing.
        if "BUSYGROUP" in str(exc):
            return
        raise
