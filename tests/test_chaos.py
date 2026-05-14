"""Chaos / recovery tests against a running docker-compose stack.

These exercise the headline reliability claim: **no task is lost** when a
worker dies mid-flight. The sweeper service reclaims stale PEL entries via
XAUTOCLAIM after the configured idle threshold and processes them.

Excluded from the default `pytest` run; invoke explicitly with:

    pytest -m chaos

Pre-requisite: `docker compose up -d` is already running on this host.
"""
import asyncio
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://localhost:8000"

BELL_QASM3 = """OPENQASM 3.0;
include "stdgates.inc";
qubit[2] q;
bit[2] c;
h q[0];
cx q[0], q[1];
c[0] = measure q[0];
c[1] = measure q[1];
"""

pytestmark = pytest.mark.chaos


def _compose(*args, check=True):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def _redis(*args, check=True):
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "redis", "redis-cli", *args],
        cwd=REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
    )


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"], check=True, capture_output=True, timeout=5
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


@pytest.fixture(autouse=True)
def stack_health_gate():
    if not _docker_available():
        pytest.skip("docker daemon not available")
    health = subprocess.run(
        ["curl", "-fsS", "-o", "/dev/null", f"{BASE_URL}/healthz"],
        capture_output=True,
    )
    if health.returncode != 0:
        pytest.skip("stack not running — start it with `docker compose up -d`")
    yield


async def test_chaos_orphaned_pel_entry_recovered_by_sweeper():
    """Construct a 'dead worker' scenario deterministically and assert the
    sweeper rescues the task.

    1. Write a state row directly into Redis (as the API would on POST).
    2. XADD the task onto the stream.
    3. Have a ghost-worker XREADGROUP the entry into the PEL — it will
       never ack (simulating a worker that crashed between claim and ack).
    4. Wait for the sweeper to XAUTOCLAIM the stale entry and process it.
    5. Verify GET /tasks/{id} reports completed with the Bell-state
       distribution.
    """
    task_id = f"chaos-{uuid.uuid4()}"

    # 1. Stop the live worker so it can't intercept the new stream entry.
    _compose("stop", "worker")
    try:
        # 2-3. Write state + enqueue.
        _redis("HSET", f"state:{task_id}",
               "status", "pending",
               "qc", BELL_QASM3,
               "shots", "1024")
        _redis("XADD", "tasks:stream", "*", "task_id", task_id)

        # 4. Ghost-worker claims into PEL; will never ack.
        _redis("XREADGROUP", "GROUP", "workers", "ghost-worker",
               "COUNT", "1", "STREAMS", "tasks:stream", ">")

        pel_before = _redis("XPENDING", "tasks:stream", "workers").stdout
        assert "ghost-worker" in pel_before, (
            f"setup failed: ghost-worker should own a PEL entry; got: {pel_before!r}"
        )
    finally:
        # 5. Restart the worker — it sees only NEW entries, never the ghost's PEL.
        _compose("start", "worker")

    # 4-5. Poll the API until the sweeper has reclaimed and completed.
    # SLA: SWEEPER_IDLE_MS (10s) + SWEEPER_INTERVAL_S (2s) + sim + slack.
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as client:
        deadline = time.monotonic() + 25
        final = None
        while time.monotonic() < deadline:
            resp = await client.get(f"/tasks/{task_id}")
            final = resp.json()
            if final.get("status") in ("completed", "error"):
                break
            await asyncio.sleep(0.5)

        assert final is not None, "polling produced no response"
        assert final.get("status") == "completed", (
            f"sweeper failed to recover within SLA: {final}\n"
            f"sweeper logs: {_compose('logs', '--tail=20', 'sweeper', check=False).stdout}"
        )

        result = final["result"]
        assert sum(result.values()) == 1024, f"shot total wrong: {result}"
        assert set(result.keys()).issubset(
            {"00", "11"}
        ), f"non-Bell outcomes: {result}"

    # PEL must be drained
    pel_after = _redis("XPENDING", "tasks:stream", "workers").stdout
    assert pel_after.strip().split("\n")[0] == "0", (
        f"PEL not drained after recovery: {pel_after}"
    )
