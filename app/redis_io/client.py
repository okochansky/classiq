import os

import redis.asyncio as aioredis

_client: aioredis.Redis | None = None


def _build() -> aioredis.Redis:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    # decode_responses=True returns str instead of bytes — our values
    # are all JSON strings, UUIDs, and status enums; bytes everywhere
    # would force a .decode() on every read.
    return aioredis.from_url(url, decode_responses=True)


async def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = _build()
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
