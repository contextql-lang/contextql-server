from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.response import QueryMeta


class ProviderCallDetail(BaseModel):
    provider_name: str
    provider_type: str
    entity_count: int
    elapsed_ms: float
    data_as_of: str | None = None


class TraceResponse(BaseModel):
    contexts_resolved: list[str]
    provider_calls: list[ProviderCallDetail]
    identity_maps_used: list[str]
    score_breakdown: dict


class ExplainRequest(BaseModel):
    query: str = Field(..., min_length=1)


class ExplainResponse(BaseModel):
    rows: list[dict]
    meta: QueryMeta
    trace: TraceResponse
