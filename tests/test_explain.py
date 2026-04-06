"""Tests for the explain endpoint."""


class TestExplainEndpoint:
    def test_explain_returns_trace(self, client):
        resp = client.post("/query/explain", json={
            "query": "SELECT invoice_id, amount FROM invoices WHERE CONTEXT IN (overdue_invoice) ORDER BY CONTEXT DESC LIMIT 5;"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "rows" in data
        assert "meta" in data
        assert "trace" in data
        trace = data["trace"]
        assert "contexts_resolved" in trace
        assert "provider_calls" in trace
        assert "identity_maps_used" in trace

    def test_explain_invalid_query(self, client):
        resp = client.post("/query/explain", json={
            "query": "NOT VALID SQL"
        })
        assert resp.status_code == 400
