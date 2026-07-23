from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_audit_service
from app.models.audit import AuditLogResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/", response_model=AuditLogResponse)
def query_audit_log(
    event_type: str | None = Query(None),
    namespace: str | None = Query(None),
    resource_type: str | None = Query(None),
    resource_name: str | None = Query(None),
    limit: int = Query(100, ge=1),
    offset: int = Query(0, ge=0),
    service=Depends(get_audit_service),
):
    entries = service.query(
        event_type=event_type,
        namespace=namespace,
        resource_type=resource_type,
        resource_name=resource_name,
        limit=limit,
        offset=offset,
    )
    return AuditLogResponse(entries=entries, total=len(entries))
