import json

from app.bootstrap import GROUP_NAME, STREAM_KEY, ensure_group
from app.domain.qasm import BELL_QASM3
from app.worker.main import run_once


async def test_worker_executes_bell_state_end_to_end(fake_redis):
    await ensure_group(fake_redis)

    task_id = "test-bell-task"
    await fake_redis.hset(
        f"state:{task_id}",
        mapping={"status": "pending", "qc": BELL_QASM3, "shots": "1024"},
    )
    await fake_redis.xadd(STREAM_KEY, {"task_id": task_id})

    processed = await run_once(fake_redis, "worker-test", block_ms=100)
    assert processed == 1

    state = await fake_redis.hgetall(f"state:{task_id}")
    assert state["status"] == "completed"
    counts = json.loads(state["result"])
    assert sum(counts.values()) == 1024
    # Bell state is a perfect EPR pair: only |00> and |11> outcomes (within shots noise).
    assert set(counts.keys()).issubset({"00", "11"})

    pending = await fake_redis.xpending(STREAM_KEY, GROUP_NAME)
    assert pending["pending"] == 0


async def test_worker_idempotent_on_terminal_state(fake_redis):
    await ensure_group(fake_redis)

    task_id = "already-done"
    await fake_redis.hset(
        f"state:{task_id}",
        mapping={"status": "completed", "result": json.dumps({"0": 1024})},
    )
    await fake_redis.xadd(STREAM_KEY, {"task_id": task_id})

    processed = await run_once(fake_redis, "worker-test", block_ms=100)
    assert processed == 1

    state = await fake_redis.hgetall(f"state:{task_id}")
    assert state["status"] == "completed"
    assert json.loads(state["result"]) == {"0": 1024}

    pending = await fake_redis.xpending(STREAM_KEY, GROUP_NAME)
    assert pending["pending"] == 0


async def test_worker_records_failure_on_bad_qasm(fake_redis):
    await ensure_group(fake_redis)

    task_id = "bad-qasm"
    await fake_redis.hset(
        f"state:{task_id}",
        mapping={"status": "pending", "qc": "this is not qasm", "shots": "16"},
    )
    await fake_redis.xadd(STREAM_KEY, {"task_id": task_id})

    processed = await run_once(fake_redis, "worker-test", block_ms=100)
    assert processed == 1

    state = await fake_redis.hgetall(f"state:{task_id}")
    assert state["status"] == "failed"
    assert "QASM3 parse failed" in state["error"]
