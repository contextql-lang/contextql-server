from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_identity_service
from app.models.identity import (
    IdentityMapCreate,
    IdentityMapListResponse,
    IdentityMapResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/identity-maps", tags=["identity-maps"])


@router.post("/", response_model=IdentityMapResponse, status_code=201)
def register_identity_map(
    body: IdentityMapCreate,
    service=Depends(get_identity_service),
):
    try:
        return service.register(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/", response_model=IdentityMapListResponse)
def list_identity_maps(
    namespace: str | None = Query(None),
    limit: int = Query(100, ge=1),
    offset: int = Query(0, ge=0),
    service=Depends(get_identity_service),
):
    maps, total = service.list(
        namespace=namespace,
        limit=limit,
        offset=offset,
    )
    return IdentityMapListResponse(identity_maps=maps, total=total)


@router.get("/{name}", response_model=IdentityMapResponse)
def describe_identity_map(
    name: str,
    namespace: str = Query("default"),
    service=Depends(get_identity_service),
):
    identity_map = service.get(name, namespace=namespace)
    if identity_map is None:
        raise HTTPException(
            status_code=404, detail=f"Identity map '{name}' not found"
        )
    return identity_map
