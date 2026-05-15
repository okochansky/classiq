"""Verify the global RedisError handler returns a spec-shaped 503 instead
of a leaking-traceback 500 when Redis is unreachable."""
from unittest.mock import AsyncMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.main import app
from app.redis_io.client import get_redis


@pytest_asyncio.fixture
async def api_with_dead_redis():
    """An api_client whose Redis dependency raises ConnectionError on every
    call — simulates the broker being unreachable."""
    dead = AsyncMock()
    dead.hgetall = AsyncMock(side_effect=RedisConnectionError("simulated outage"))
    dead.hset = AsyncMock(side_effect=RedisConnectionError("simulated outage"))
    dead.xadd = AsyncMock(side_effect=RedisConnectionError("simulated outage"))

    async def _override():
        return dead

    app.dependency_overrides[get_redis] = _override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


async def test_post_returns_503_when_redis_down(api_with_dead_redis):
    response = await api_with_dead_redis.post(
        "/tasks", json={"qc": "OPENQASM 3.0;"}
    )
    assert response.status_code == 503
    body = response.json()
    assert body == {
        "status": "error",
        "message": "Backend unavailable. Please retry.",
    }


async def test_get_returns_503_when_redis_down(api_with_dead_redis):
    response = await api_with_dead_redis.get("/tasks/anything")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert "Backend unavailable" in body["message"]
