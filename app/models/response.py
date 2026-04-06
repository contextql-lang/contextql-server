from __future__ import annotations

from pydantic import BaseModel


class QueryMeta(BaseModel):
    execution_time_ms: float
    row_count: int
    columns: list[str]
    generated_sql: str
    diagnostics: list[str]


class QueryResponse(BaseModel):
    rows: list[dict]
    meta: QueryMeta


class ErrorDetail(BaseModel):
    error: str
    message: str
    detail: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    engine_version: str
    tables: list[str]
    contexts: list[str]
    catalog_contexts: int = 0
    registered_providers: int = 0
    identity_maps: int = 0
