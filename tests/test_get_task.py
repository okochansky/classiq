import json


async def test_get_unknown_task_returns_error(api_client):
    response = await api_client.get("/tasks/does-not-exist")
    assert response.status_code == 200
    assert response.json() == {"status": "error", "message": "Task not found."}


async def test_get_pending_task(api_client, fake_redis):
    await fake_redis.hset("state:abc", mapping={"status": "pending", "qc": "x"})
    response = await api_client.get("/tasks/abc")
    assert response.status_code == 200
    assert response.json() == {
        "status": "pending",
        "message": "Task is still in progress.",
    }


async def test_get_running_task_reports_pending(api_client, fake_redis):
    await fake_redis.hset("state:r", mapping={"status": "running", "qc": "x"})
    response = await api_client.get("/tasks/r")
    assert response.json()["status"] == "pending"


async def test_get_completed_task_returns_result(api_client, fake_redis):
    await fake_redis.hset(
        "state:done",
        mapping={"status": "completed", "result": json.dumps({"00": 512, "11": 512})},
    )
    response = await api_client.get("/tasks/done")
    assert response.status_code == 200
    assert response.json() == {
        "status": "completed",
        "result": {"00": 512, "11": 512},
    }


async def test_get_failed_task_reports_error(api_client, fake_redis):
    await fake_redis.hset(
        "state:bad", mapping={"status": "failed", "error": "boom"}
    )
    response = await api_client.get("/tasks/bad")
    assert response.status_code == 200
    assert response.json() == {"status": "error", "message": "boom"}
