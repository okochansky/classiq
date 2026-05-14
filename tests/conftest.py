import fakeredis.aioredis as fakeredis_aioredis
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.redis_io.client import get_redis


@pytest_asyncio.fixture
async def fake_redis():
    client = fakeredis_aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def api_client(fake_redis):
    async def _override():
        return fake_redis

    app.dependency_overrides[get_redis] = _override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
