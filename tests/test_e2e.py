"""End-to-end integration tests that exercise the full API ↔ worker loop
against the fakeredis-backed app. Complements the unit tests which probe
each layer in isolation.
"""
from app.bootstrap import ensure_group
from app.domain.qasm import BELL_QASM3
from app.worker.main import run_once


async def test_full_lifecycle_post_then_worker_then_get(api_client, fake_redis):
    """POST /tasks → GET reports pending → worker runs → GET reports completed."""
    await ensure_group(fake_redis)

    resp = await api_client.post("/tasks", json={"qc": BELL_QASM3, "shots": 256})
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]

    resp = await api_client.get(f"/tasks/{task_id}")
    assert resp.json() == {"status": "pending", "message": "Task is still in progress."}

    processed = await run_once(fake_redis, "worker-test", block_ms=100)
    assert processed == 1

    resp = await api_client.get(f"/tasks/{task_id}")
    body = resp.json()
    assert body["status"] == "completed"
    assert sum(body["result"].values()) == 256
    assert set(body["result"].keys()).issubset({"00", "11"})


async def test_concurrent_submissions_all_complete_with_distinct_ids(api_client, fake_redis):
    """Five tasks submitted back-to-back all receive unique task_ids and
    all complete with valid results when the worker drains the queue."""
    await ensure_group(fake_redis)

    task_ids = []
    for _ in range(5):
        resp = await api_client.post(
            "/tasks", json={"qc": BELL_QASM3, "shots": 128}
        )
        assert resp.status_code == 202
        task_ids.append(resp.json()["task_id"])

    assert len(set(task_ids)) == 5, "task_ids must be distinct"

    while await run_once(fake_redis, "worker-test", block_ms=10) > 0:
        pass

    for tid in task_ids:
        body = (await api_client.get(f"/tasks/{tid}")).json()
        assert body["status"] == "completed", f"{tid}: {body}"
        assert sum(body["result"].values()) == 128


async def test_openapi_schema_documents_task_endpoints(api_client):
    resp = await api_client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()

    assert "/tasks" in spec["paths"]
    post = spec["paths"]["/tasks"]["post"]
    assert "202" in post["responses"]

    assert "/tasks/{task_id}" in spec["paths"]
    assert "get" in spec["paths"]["/tasks/{task_id}"]


async def test_get_unknown_task_does_not_create_state(api_client, fake_redis):
    """GET on a non-existent task_id must not have side effects."""
    keys_before = await fake_redis.keys("state:*")
    resp = await api_client.get("/tasks/never-existed")
    assert resp.json() == {"status": "error", "message": "Task not found."}
    keys_after = await fake_redis.keys("state:*")
    assert keys_before == keys_after
