from __future__ import annotations

from contextql.providers import MCPResult, RemoteResult


class EchoMCPProvider:
    """Returns sequential entity IDs with linear scores. Useful for testing."""

    def resolve(
        self,
        entity_type: str,
        params: dict,
        limit: int | None = None,
    ) -> MCPResult:
        count = limit or int(params.get("count", 10))
        entity_ids = list(range(1, count + 1))
        scores = [round(i / count, 4) for i in range(1, count + 1)]
        return MCPResult(entity_type=entity_type, entity_ids=entity_ids, scores=scores)


class StaticRemoteProvider:
    """Returns a fixed set of rows regardless of query parameters."""

    ROWS = [
        {"item_id": "STATIC-1", "status": "active", "value": 100},
        {"item_id": "STATIC-2", "status": "pending", "value": 250},
        {"item_id": "STATIC-3", "status": "closed", "value": 50},
    ]

    def query(
        self,
        resource: str,
        filters: dict,
        columns: list[str],
        limit: int | None = None,
    ) -> RemoteResult:
        rows = self.ROWS[:limit] if limit else self.ROWS
        return RemoteResult(rows=rows)
