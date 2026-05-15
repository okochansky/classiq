import uuid


async def test_post_returns_task_id_and_persists_state(api_client, fake_redis):
    response = await api_client.post("/tasks", json={"qc": "OPENQASM 3.0;"})
    assert response.status_code == 202
    body = response.json()
    assert body["message"] == "Task submitted successfully."
    task_id = body["task_id"]
    uuid.UUID(task_id)

    state = await fake_redis.hgetall(f"state:{task_id}")
    assert state["status"] == "pending"
    assert state["qc"] == "OPENQASM 3.0;"
    assert state["shots"] == "1024"

    entries = await fake_redis.xrange("tasks:stream")
    assert len(entries) == 1
    _entry_id, fields = entries[0]
    assert fields["task_id"] == task_id


async def test_post_rejects_empty_qc(api_client):
    response = await api_client.post("/tasks", json={"qc": ""})
    assert response.status_code == 422


async def test_post_accepts_custom_shots(api_client, fake_redis):
    response = await api_client.post(
        "/tasks", json={"qc": "OPENQASM 3.0;", "shots": 256}
    )
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    state = await fake_redis.hgetall(f"state:{task_id}")
    assert state["shots"] == "256"


async def test_post_rejects_out_of_range_shots(api_client):
    response = await api_client.post(
        "/tasks", json={"qc": "OPENQASM 3.0;", "shots": 0}
    )
    assert response.status_code == 422


async def test_post_rejects_oversized_qc(api_client):
    """1 MiB cap on the qc payload — DoS guardrail."""
    response = await api_client.post(
        "/tasks", json={"qc": "x" * (1_048_577)}
    )
    assert response.status_code == 422
