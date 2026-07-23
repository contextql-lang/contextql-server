from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ProviderCreate(BaseModel):
    name: str
    provider_type: Literal["MCP", "REMOTE"]
    namespace: str = "default"
    endpoint: str | None = None
    timeout_ms: int = 30000
    entity_key_type: str | None = None
    trust_tier: str = "standard"


class ProviderUpdate(BaseModel):
    endpoint: str | None = None
    timeout_ms: int | None = None
    entity_key_type: str | None = None
    trust_tier: str | None = None


class ProviderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    namespace: str
    provider_type: str
    endpoint: str | None = None
    credentials_ref: str | None = None
    timeout_ms: int
    health_state: str
    entity_key_type: str | None = None
    resource_shape: str | None = None
    trust_tier: str
    enabled: bool
    registered_at: str
    updated_at: str
    last_success_at: str | None = None
    last_failure_at: str | None = None


class ProviderListResponse(BaseModel):
    providers: list[ProviderResponse]
    total: int
