from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_provider_service
from app.models.provider import (
    ProviderCreate,
    ProviderListResponse,
    ProviderResponse,
    ProviderUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["providers"])


@router.post("/", response_model=ProviderResponse, status_code=201)
def register_provider(
    body: ProviderCreate,
    service=Depends(get_provider_service),
):
    try:
        return service.register(**body.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/", response_model=ProviderListResponse)
def list_providers(
    namespace: str | None = Query(None),
    provider_type: str | None = Query(None),
    limit: int = Query(100, ge=1),
    offset: int = Query(0, ge=0),
    service=Depends(get_provider_service),
):
    providers, total = service.list(
        namespace=namespace,
        provider_type=provider_type,
        limit=limit,
        offset=offset,
    )
    return ProviderListResponse(providers=providers, total=total)


@router.get("/{name}", response_model=ProviderResponse)
def describe_provider(
    name: str,
    namespace: str = Query("default"),
    service=Depends(get_provider_service),
):
    provider = service.get(name, namespace=namespace)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return provider


@router.put("/{name}", response_model=ProviderResponse)
def update_provider(
    name: str,
    body: ProviderUpdate,
    namespace: str = Query("default"),
    service=Depends(get_provider_service),
):
    existing = service.get(name, namespace=namespace)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    try:
        updates = body.model_dump(exclude_unset=True)
        return service.update(name, namespace=namespace, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{name}/enable", response_model=ProviderResponse)
def enable_provider(
    name: str,
    namespace: str = Query("default"),
    service=Depends(get_provider_service),
):
    existing = service.get(name, namespace=namespace)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return service.enable(name, namespace=namespace)


@router.post("/{name}/disable", response_model=ProviderResponse)
def disable_provider(
    name: str,
    namespace: str = Query("default"),
    service=Depends(get_provider_service),
):
    existing = service.get(name, namespace=namespace)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return service.disable(name, namespace=namespace)


@router.get("/{name}/health", response_model=ProviderResponse)
def provider_health(
    name: str,
    namespace: str = Query("default"),
    service=Depends(get_provider_service),
):
    provider = service.get(name, namespace=namespace)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Provider '{name}' not found")
    return provider
