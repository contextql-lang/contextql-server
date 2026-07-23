from __future__ import annotations

import math
import time
from dataclasses import dataclass

import contextql as cql

from app.models.response import QueryMeta, QueryResponse


@dataclass
class QueryResult:
    """Internal result holder that preserves trace alongside the response."""
    rows: list[dict]
    meta: QueryMeta
    trace: object | None = None  # contextql ContextTrace if available


class QueryService:
    def __init__(self, engine: cql.Engine) -> None:
        self._engine = engine

    def execute(self, query: str) -> QueryResult:
        start = time.perf_counter()
        result = self._engine.execute(query)
        elapsed_ms = (time.perf_counter() - start) * 1000

        rows = result.to_pandas().to_dict(orient="records")
        for row in rows:
            for key, val in row.items():
                if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    row[key] = None

        meta = QueryMeta(
            execution_time_ms=round(elapsed_ms, 2),
            row_count=result.row_count,
            columns=result.columns,
            generated_sql=result.sql,
            diagnostics=[str(d) for d in result.diagnostics],
        )

        trace = getattr(result, "trace", None)
        return QueryResult(rows=rows, meta=meta, trace=trace)

    def execute_response(self, query: str) -> QueryResponse:
        """Execute and return only the QueryResponse (for backward compat)."""
        qr = self.execute(query)
        return QueryResponse(rows=qr.rows, meta=qr.meta)
