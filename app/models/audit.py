from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: str
    event_type: str
    actor: str
    namespace: str
    resource_type: str | None = None
    resource_name: str | None = None
    detail: dict | None = None
    trace_id: str | None = None


class AuditLogResponse(BaseModel):
    entries: list[AuditEntry]
    total: int
