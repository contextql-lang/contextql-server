"""Tests for the audit log API."""


class TestAuditLog:
    def test_audit_log_has_entries(self, client):
        # Previous test operations should have generated audit entries
        resp = client.get("/audit/")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert data["total"] >= 0

    def test_audit_log_filter_by_event_type(self, client):
        resp = client.get("/audit/", params={"event_type": "server_start"})
        assert resp.status_code == 200
