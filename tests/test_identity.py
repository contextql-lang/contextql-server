"""Tests for the identity map registry API."""
import uuid


def _unique_name(prefix="idmap"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class TestIdentityRegistry:
    def test_register_identity_map(self, client):
        name = _unique_name()
        resp = client.post("/identity-maps/", json={
            "name": name,
            "source_system": "invoices",
            "source_entity_path": "invoices.vendor_id",
            "target_system": "vendors",
            "target_entity_path": "vendors.vendor_id",
            "description": "Bridge invoices to vendors by vendor_id",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == name
        assert data["matching_mode"] == "exact"
        assert data["confidence"] == 1.0

    def test_list_identity_maps(self, client):
        resp = client.get("/identity-maps/")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["total"], int)

    def test_get_identity_map(self, client):
        name = _unique_name()
        client.post("/identity-maps/", json={
            "name": name,
            "source_system": "orders",
            "source_entity_path": "orders.vendor_id",
            "target_system": "vendors",
            "target_entity_path": "vendors.vendor_id",
        })
        resp = client.get(f"/identity-maps/{name}")
        assert resp.status_code == 200
        assert resp.json()["name"] == name

    def test_get_nonexistent_returns_404(self, client):
        resp = client.get("/identity-maps/nonexistent_xyz")
        assert resp.status_code == 404
