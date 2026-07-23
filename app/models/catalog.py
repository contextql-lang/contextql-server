from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ContextCreate(BaseModel):
    name: str
    definition_text: str
    entity_key: str
    namespace: str = "default"
    has_score: bool = False
    score_column: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    classification: str = "internal"


class ContextUpdate(BaseModel):
    definition_text: str | None = None
    entity_key: str | None = None
    has_score: bool | None = None
    score_column: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    classification: str | None = None


class ContextResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    namespace: str
    version: int
    definition_text: str
    entity_key: str
    has_score: bool
    score_column: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    classification: str | None = None
    lifecycle_state: str
    dependency_refs: list[str] | None = None
    provider_refs: list[str] | None = None
    created_at: str
    updated_at: str
    last_validated_at: str | None = None
    last_executed_at: str | None = None
    freshness_metadata: dict | None = None
    context_id: str | None = None
    definition_hash: str | None = None
    materialization_json: str | None = None
    current_snapshot_version: int | None = None
    data_as_of: str | None = None
    last_refresh_error: str | None = None


class ContextListResponse(BaseModel):
    contexts: list[ContextResponse]
    total: int
