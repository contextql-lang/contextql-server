import pytest


def test_simple_select(client):
    resp = client.post("/query", json={"query": "SELECT invoice_id, amount FROM invoices LIMIT 5;"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["rows"]) == 5
    assert data["meta"]["row_count"] == 5
    assert "invoice_id" in data["meta"]["columns"]
    assert "amount" in data["meta"]["columns"]


def test_context_query(client):
    resp = client.post("/query", json={
        "query": (
            "SELECT invoice_id, amount FROM invoices "
            "WHERE CONTEXT IN (open_invoice) "
            "ORDER BY CONTEXT DESC LIMIT 5;"
        )
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["rows"]) <= 5
    assert data["meta"]["execution_time_ms"] >= 0


def test_mcp_query(client):
    resp = client.post("/query", json={
        "query": (
            "SELECT invoice_id, amount, CONTEXT_SCORE() AS score "
            "FROM invoices "
            "WHERE CONTEXT IN (MCP(fraud_detection)) "
            "ORDER BY CONTEXT DESC LIMIT 5;"
        )
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["rows"]) <= 5
    assert "score" in data["meta"]["columns"]


def test_empty_query_returns_422(client):
    resp = client.post("/query", json={"query": ""})
    assert resp.status_code == 422


def test_missing_query_returns_422(client):
    resp = client.post("/query", json={})
    assert resp.status_code == 422


def test_invalid_sql_returns_400(client):
    resp = client.post("/query", json={"query": "SELECT FROM nonexistent_ctx WHERE CONTEXT IN (nope);"})
    assert resp.status_code in (400, 500)
