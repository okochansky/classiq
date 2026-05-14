import json

from app.bootstrap import GROUP_NAME, STREAM_KEY, ensure_group
from app.domain.qasm import BELL_QASM3
from app.sweeper.main import sweep_once


async def test_sweeper_reclaims_stale_pel_entry_and_processes(fake_redis):
    """Simulates a crashed worker: an XREADGROUP claim that never ack'd.
    The sweeper should reclaim via XAUTOCLAIM and complete the task."""
    await ensure_group(fake_redis)

    task_id = "stale-task"
    await fake_redis.hset(
        f"state:{task_id}",
        mapping={"status": "pending", "qc": BELL_QASM3, "shots": "1024"},
    )
    await fake_redis.xadd(STREAM_KEY, {"task_id": task_id})

    # A worker reads the entry into its PEL but never acks (simulating crash).
    delivered = await fake_redis.xreadgroup(
        GROUP_NAME, "worker-doomed", {STREAM_KEY: ">"}, count=1, block=10
    )
    assert delivered, "fixture: worker should have read the entry"
    pending_before = await fake_redis.xpending(STREAM_KEY, GROUP_NAME)
    assert pending_before["pending"] == 1

    # Sweep with idle_ms=0 so the entry is immediately stale.
    processed = await sweep_once(fake_redis, "sweeper-test", idle_ms=0)
    assert processed == 1

    state = await fake_redis.hgetall(f"state:{task_id}")
    assert state["status"] == "completed"
    counts = json.loads(state["result"])
    assert sum(counts.values()) == 1024
    assert set(counts.keys()).issubset({"00", "11"})

    pending_after = await fake_redis.xpending(STREAM_KEY, GROUP_NAME)
    assert pending_after["pending"] == 0


async def test_sweeper_caps_retries_and_marks_failed(fake_redis):
    """When a task has already burned through its max_attempts budget, the
    next sweeper reclaim must terminate it as failed rather than loop forever."""
    await ensure_group(fake_redis)

    task_id = "doomed-task"
    await fake_redis.hset(
        f"state:{task_id}",
        mapping={
            "status": "pending",
            "qc": BELL_QASM3,
            "shots": "16",
            # already at max budget; next increment will exceed
            "attempts": "3",
        },
    )
    await fake_redis.xadd(STREAM_KEY, {"task_id": task_id})
    await fake_redis.xreadgroup(
        GROUP_NAME, "worker-doomed", {STREAM_KEY: ">"}, count=1, block=10
    )

    processed = await sweep_once(fake_redis, "sweeper-test", idle_ms=0)
    assert processed == 1

    state = await fake_redis.hgetall(f"state:{task_id}")
    assert state["status"] == "failed"
    assert "max_retries_exceeded" in state["error"]

    pending = await fake_redis.xpending(STREAM_KEY, GROUP_NAME)
    assert pending["pending"] == 0


async def test_sweeper_skips_already_terminal_state(fake_redis):
    """If a task became terminal (e.g. worker finished but crashed before ack),
    the sweeper must idempotently ack rather than re-execute."""
    await ensure_group(fake_redis)

    task_id = "completed-but-unacked"
    await fake_redis.hset(
        f"state:{task_id}",
        mapping={
            "status": "completed",
            "result": json.dumps({"0": 512, "1": 512}),
        },
    )
    await fake_redis.xadd(STREAM_KEY, {"task_id": task_id})
    await fake_redis.xreadgroup(
        GROUP_NAME, "worker-doomed", {STREAM_KEY: ">"}, count=1, block=10
    )

    processed = await sweep_once(fake_redis, "sweeper-test", idle_ms=0)
    assert processed == 1

    state = await fake_redis.hgetall(f"state:{task_id}")
    # Result unchanged — no re-execution
    assert state["status"] == "completed"
    assert json.loads(state["result"]) == {"0": 512, "1": 512}

    pending = await fake_redis.xpending(STREAM_KEY, GROUP_NAME)
    assert pending["pending"] == 0


async def test_sweeper_returns_zero_when_no_stale_entries(fake_redis):
    await ensure_group(fake_redis)
    processed = await sweep_once(fake_redis, "sweeper-test", idle_ms=0)
    assert processed == 0
