"""Tests for the context catalog API."""
import uuid


def _unique_name(prefix="ctx"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class TestCatalogCRUD:
    def test_create_context(self, client):
        name = _unique_name()
        resp = client.post("/contexts/", json={
            "name": name,
            "definition_text": "SELECT invoice_id FROM invoices WHERE status = 'open'",
            "entity_key": "invoice_id",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == name
        assert data["lifecycle_state"] == "draft"
        assert data["version"] == 1
        assert data["namespace"] == "default"

    def test_list_contexts(self, client):
        resp = client.get("/contexts/")
        assert resp.status_code == 200
        data = resp.json()
        assert "contexts" in data
        assert isinstance(data["total"], int)

    def test_get_context(self, client):
        name = _unique_name()
        client.post("/contexts/", json={
            "name": name,
            "definition_text": "SELECT invoice_id FROM invoices",
            "entity_key": "invoice_id",
        })
        resp = client.get(f"/contexts/{name}")
        assert resp.status_code == 200
        assert resp.json()["name"] == name

    def test_get_nonexistent_returns_404(self, client):
        resp = client.get("/contexts/nonexistent_xyz")
        assert resp.status_code == 404

    def test_update_creates_new_version(self, client):
        name = _unique_name()
        client.post("/contexts/", json={
            "name": name,
            "definition_text": "SELECT invoice_id FROM invoices",
            "entity_key": "invoice_id",
        })
        resp = client.put(f"/contexts/{name}", json={
            "description": "Updated context",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 2
        assert data["lifecycle_state"] == "draft"

    def test_list_versions(self, client):
        name = _unique_name()
        client.post("/contexts/", json={
            "name": name,
            "definition_text": "SELECT invoice_id FROM invoices",
            "entity_key": "invoice_id",
        })
        client.put(f"/contexts/{name}", json={"description": "v2"})
        resp = client.get(f"/contexts/{name}/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2


class TestCatalogLifecycle:
    def test_validate_context(self, client):
        name = _unique_name("lc")
        client.post("/contexts/", json={
            "name": name,
            "definition_text": "SELECT invoice_id FROM invoices WHERE status = 'open'",
            "entity_key": "invoice_id",
        })
        resp = client.post(f"/contexts/{name}/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lifecycle_state"] == "validated"

    def test_activate_context(self, client):
        name = _unique_name("act")
        client.post("/contexts/", json={
            "name": name,
            "definition_text": "SELECT invoice_id FROM invoices WHERE status = 'open'",
            "entity_key": "invoice_id",
        })
        resp = client.post(f"/contexts/{name}/activate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lifecycle_state"] == "active"

    def test_retire_context(self, client):
        name = _unique_name("ret")
        client.post("/contexts/", json={
            "name": name,
            "definition_text": "SELECT invoice_id FROM invoices WHERE status = 'open'",
            "entity_key": "invoice_id",
        })
        client.post(f"/contexts/{name}/activate")
        resp = client.post(f"/contexts/{name}/retire")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lifecycle_state"] == "retired"

    def test_delete_draft_only(self, client):
        name = _unique_name("del")
        client.post("/contexts/", json={
            "name": name,
            "definition_text": "SELECT 1 AS id",
            "entity_key": "id",
        })
        resp = client.delete(f"/contexts/{name}")
        assert resp.status_code == 204
