import pytest

from app.services.query_service import QueryService


@pytest.fixture(scope="session")
def query_service(engine):
    return QueryService(engine)


def test_execute_returns_query_response(query_service):
    result = query_service.execute("SELECT invoice_id FROM invoices LIMIT 3;")
    assert len(result.rows) == 3
    assert result.meta.row_count == 3
    assert result.meta.columns == ["invoice_id"]
    assert result.meta.execution_time_ms >= 0
    assert isinstance(result.meta.generated_sql, str)


def test_execute_context_query(query_service):
    result = query_service.execute(
        "SELECT invoice_id, amount FROM invoices "
        "WHERE CONTEXT IN (open_invoice) LIMIT 10;"
    )
    assert result.meta.row_count <= 10
    assert "invoice_id" in result.meta.columns


def test_execute_invalid_query_raises(query_service):
    with pytest.raises(Exception):
        query_service.execute("TOTALLY INVALID GARBAGE;")
