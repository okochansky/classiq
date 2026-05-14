import fakeredis.aioredis as fakeredis_aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.redis_io.client import get_redis


def pytest_addoption(parser):
    parser.addoption(
        "--run-chaos",
        action="store_true",
        default=False,
        help="run chaos tests against a live `docker compose` stack",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-chaos"):
        return
    skip_chaos = pytest.mark.skip(
        reason="needs --run-chaos and a live `docker compose up -d` stack"
    )
    for item in items:
        if "chaos" in item.keywords:
            item.add_marker(skip_chaos)


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
