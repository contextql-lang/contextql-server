from __future__ import annotations

import math
import json
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
    def __init__(
        self,
        engine: cql.Engine,
        *,
        max_result_rows: int = 10_000,
        max_response_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if max_result_rows <= 0 or max_response_bytes <= 0:
            raise ValueError("Query result limits must be positive.")
        self._engine = engine
        self._max_result_rows = max_result_rows
        self._max_response_bytes = max_response_bytes

    def _validate_select_limit(self, query: str) -> None:
        from contextql.semantic import QueryModel, analyze_sql

        analysis = analyze_sql(query, self._engine._catalog)
        if not analysis.ok or not analysis.statements:
            return
        statement = analysis.statements[0]
        if not isinstance(statement, QueryModel):
            return
        if statement.limit is None:
            raise ValueError(
                "[E305] server SELECT queries require an explicit LIMIT."
            )
        if statement.limit > self._max_result_rows:
            raise ValueError(
                "[E305] query LIMIT exceeds the configured maximum of "
                f"{self._max_result_rows} rows."
            )

    def execute(self, query: str) -> QueryResult:
        self._validate_select_limit(query)
        start = time.perf_counter()
        result = self._engine.execute(query)
        elapsed_ms = (time.perf_counter() - start) * 1000

        rows = result.to_pandas().to_dict(orient="records")
        for row in rows:
            for key, val in row.items():
                if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    row[key] = None
        encoded_size = 2
        for row in rows:
            encoded_size += len(
                json.dumps(
                    row, separators=(",", ":"), default=str
                ).encode("utf-8")
            ) + 1
            if encoded_size > self._max_response_bytes:
                raise ValueError(
                    "[E306] encoded query response exceeds the configured "
                    f"maximum of {self._max_response_bytes} bytes."
                )

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
