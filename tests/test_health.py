def test_health_returns_200(client):
    resp = client.get("/health")
    assert resp.status_code == 200


def test_health_fields(client):
    data = client.get("/health").json()
    assert data["status"] == "ok"
    assert data["version"] == "0.3.0"
    assert "engine_version" in data
    assert isinstance(data["tables"], list)
    assert isinstance(data["contexts"], list)
    assert len(data["tables"]) >= 6
    assert len(data["contexts"]) >= 9
