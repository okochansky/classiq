async def test_healthz_returns_alive(api_client):
    response = await api_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
