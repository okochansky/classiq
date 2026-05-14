from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_healthz_returns_alive():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_post_tasks_returns_task_id_and_message():
    response = client.post("/tasks", json={"qc": "OPENQASM 3.0;"})
    assert response.status_code == 202
    body = response.json()
    assert "task_id" in body
    assert body["message"] == "Task submitted successfully."


def test_get_tasks_stub_returns_501():
    response = client.get("/tasks/nonexistent")
    assert response.status_code == 501
