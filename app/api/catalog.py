from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_catalog_service
from app.models.catalog import (
    ContextCreate,
    ContextListResponse,
    ContextResponse,
    ContextUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contexts", tags=["contexts"])


@router.post("/", response_model=ContextResponse, status_code=201)
def create_context(
    body: ContextCreate,
    catalog=Depends(get_catalog_service),
):
    try:
        context = catalog.create(**body.model_dump())
        return context
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/", response_model=ContextListResponse)
def list_contexts(
    namespace: str | None = Query(None),
    lifecycle_state: str | None = Query(None),
    limit: int = Query(100, ge=1),
    offset: int = Query(0, ge=0),
    catalog=Depends(get_catalog_service),
):
    contexts, total = catalog.list(
        namespace=namespace,
        lifecycle_state=lifecycle_state,
        limit=limit,
        offset=offset,
    )
    return ContextListResponse(contexts=contexts, total=total)


@router.get("/{name}", response_model=ContextResponse)
def get_context(
    name: str,
    namespace: str = Query("default"),
    catalog=Depends(get_catalog_service),
):
    context = catalog.get(name, namespace=namespace)
    if context is None:
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")
    return context


@router.put("/{name}", response_model=ContextResponse)
def update_context(
    name: str,
    body: ContextUpdate,
    namespace: str = Query("default"),
    catalog=Depends(get_catalog_service),
):
    existing = catalog.get(name, namespace=namespace)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")
    try:
        updates = body.model_dump(exclude_unset=True)
        context = catalog.update(name, namespace=namespace, **updates)
        return context
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{name}/validate", response_model=ContextResponse)
def validate_context(
    name: str,
    namespace: str = Query("default"),
    catalog=Depends(get_catalog_service),
):
    existing = catalog.get(name, namespace=namespace)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")
    try:
        return catalog.validate(name, namespace=namespace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{name}/activate", response_model=ContextResponse)
def activate_context(
    name: str,
    namespace: str = Query("default"),
    catalog=Depends(get_catalog_service),
):
    existing = catalog.get(name, namespace=namespace)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")
    try:
        return catalog.activate(name, namespace=namespace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{name}/retire", response_model=ContextResponse)
def retire_context(
    name: str,
    namespace: str = Query("default"),
    catalog=Depends(get_catalog_service),
):
    existing = catalog.get(name, namespace=namespace)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")
    try:
        return catalog.retire(name, namespace=namespace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{name}", status_code=204)
def delete_context(
    name: str,
    namespace: str = Query("default"),
    catalog=Depends(get_catalog_service),
):
    context = catalog.get(name, namespace=namespace)
    if context is None:
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")
    if context.get("lifecycle_state") != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete context in '{context.get('lifecycle_state')}' state; only draft contexts can be deleted",
        )
    catalog.delete(name, namespace=namespace)


@router.get("/{name}/versions", response_model=list[ContextResponse])
def list_versions(
    name: str,
    namespace: str = Query("default"),
    catalog=Depends(get_catalog_service),
):
    return catalog.versions(name, namespace=namespace)


@router.post("/{name}/preview")
def preview_context(
    name: str,
    namespace: str = Query("default"),
    limit: int = Query(10, ge=1),
    catalog=Depends(get_catalog_service),
):
    context = catalog.get(name, namespace=namespace)
    if context is None:
        raise HTTPException(status_code=404, detail=f"Context '{name}' not found")
    try:
        rows = catalog.preview(name, namespace=namespace, limit=limit)
        return {"rows": rows}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
