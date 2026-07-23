"""Tests for the provider registry API."""
import uuid


def _unique_name(prefix="prov"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class TestProviderRegistry:
    def test_register_provider(self, client):
        name = _unique_name("mcp")
        resp = client.post("/providers/", json={
            "name": name,
            "provider_type": "MCP",
            "endpoint": "http://localhost:9000/fraud",
            "entity_key_type": "INTEGER",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == name
        assert data["provider_type"] == "MCP"
        assert data["enabled"] is True
        assert data["health_state"] == "unknown"

    def test_register_remote_provider(self, client):
        name = _unique_name("remote")
        resp = client.post("/providers/", json={
            "name": name,
            "provider_type": "REMOTE",
            "endpoint": "https://jira.example.com/api",
        })
        assert resp.status_code == 201

    def test_list_providers(self, client):
        resp = client.get("/providers/")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["total"], int)

    def test_get_provider(self, client):
        name = _unique_name("get")
        client.post("/providers/", json={
            "name": name,
            "provider_type": "MCP",
        })
        resp = client.get(f"/providers/{name}")
        assert resp.status_code == 200
        assert resp.json()["name"] == name

    def test_disable_enable_provider(self, client):
        name = _unique_name("toggle")
        client.post("/providers/", json={
            "name": name,
            "provider_type": "MCP",
        })
        resp = client.post(f"/providers/{name}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        resp = client.post(f"/providers/{name}/enable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    def test_get_health(self, client):
        name = _unique_name("health")
        client.post("/providers/", json={
            "name": name,
            "provider_type": "MCP",
        })
        resp = client.get(f"/providers/{name}/health")
        assert resp.status_code == 200
        assert "health_state" in resp.json()
