from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class IdentityMapCreate(BaseModel):
    name: str
    source_system: str
    source_entity_path: str
    target_system: str
    target_entity_path: str
    namespace: str = "default"
    matching_mode: str = "exact"
    confidence: float = 1.0
    description: str | None = None


class IdentityMapResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    namespace: str
    source_system: str
    source_entity_path: str
    target_system: str
    target_entity_path: str
    matching_mode: str
    confidence: float
    description: str | None = None
    version: int
    created_at: str
    updated_at: str


class IdentityMapListResponse(BaseModel):
    identity_maps: list[IdentityMapResponse]
    total: int
