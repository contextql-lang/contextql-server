from __future__ import annotations

import contextql as cql

from app.config import settings
from app.services.audit_service import AuditService
from app.services.catalog_service import CatalogService
from app.services.identity_service import IdentityService
from app.services.provider_service import ProviderService
from app.services.query_service import QueryService

_engine: cql.Engine | None = None
_query_service: QueryService | None = None
_catalog_service: CatalogService | None = None
_provider_service: ProviderService | None = None
_identity_service: IdentityService | None = None
_audit_service: AuditService | None = None


def init_services(
    engine: cql.Engine,
    catalog: CatalogService,
    provider: ProviderService,
    identity: IdentityService,
    audit: AuditService,
) -> None:
    global _engine, _query_service
    global _catalog_service, _provider_service, _identity_service, _audit_service
    _engine = engine
    _query_service = QueryService(
        engine,
        max_result_rows=settings.max_query_rows,
        max_response_bytes=settings.max_query_response_bytes,
    )
    _catalog_service = catalog
    _provider_service = provider
    _identity_service = identity
    _audit_service = audit


def set_engine(engine: cql.Engine) -> None:
    """Backward-compatible setter for engine + query service only."""
    global _engine, _query_service
    _engine = engine
    _query_service = QueryService(
        engine,
        max_result_rows=settings.max_query_rows,
        max_response_bytes=settings.max_query_response_bytes,
    )


def get_engine() -> cql.Engine:
    if _engine is None:
        raise RuntimeError("Engine not initialized")
    return _engine


def get_query_service() -> QueryService:
    if _query_service is None:
        raise RuntimeError("QueryService not initialized")
    return _query_service


def get_catalog_service() -> CatalogService:
    if _catalog_service is None:
        raise RuntimeError("CatalogService not initialized")
    return _catalog_service


def get_provider_service() -> ProviderService:
    if _provider_service is None:
        raise RuntimeError("ProviderService not initialized")
    return _provider_service


def get_identity_service() -> IdentityService:
    if _identity_service is None:
        raise RuntimeError("IdentityService not initialized")
    return _identity_service


def get_audit_service() -> AuditService:
    if _audit_service is None:
        raise RuntimeError("AuditService not initialized")
    return _audit_service
