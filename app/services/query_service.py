from __future__ import annotations

import math
import time

import contextql as cql

from app.models.response import QueryMeta, QueryResponse


class QueryService:
    def __init__(self, engine: cql.Engine) -> None:
        self._engine = engine

    def execute(self, query: str) -> QueryResponse:
        start = time.perf_counter()
        result = self._engine.execute(query)
        elapsed_ms = (time.perf_counter() - start) * 1000

        rows = result.to_pandas().to_dict(orient="records")
        # Replace NaN/inf with None for JSON safety
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
        return QueryResponse(rows=rows, meta=meta)
