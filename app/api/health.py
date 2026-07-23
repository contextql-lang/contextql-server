from fastapi import APIRouter, Depends

import contextql as cql

from app.dependencies import (
    get_catalog_service,
    get_engine,
    get_identity_service,
    get_provider_service,
)
from app.models.response import HealthResponse
from app.services.catalog_service import CatalogService
from app.services.identity_service import IdentityService
from app.services.provider_service import ProviderService

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(
    engine: cql.Engine = Depends(get_engine),
    catalog: CatalogService = Depends(get_catalog_service),
    providers: ProviderService = Depends(get_provider_service),
    identity: IdentityService = Depends(get_identity_service),
) -> HealthResponse:
    _, catalog_count = catalog.list(limit=0)
    _, provider_count = providers.list(limit=0)
    _, identity_count = identity.list(limit=0)

    return HealthResponse(
        status="ok",
        version="0.3.0",
        engine_version=cql.__version__,
        tables=engine.catalog.tables(),
        contexts=engine.catalog.contexts(),
        catalog_contexts=catalog_count,
        registered_providers=provider_count,
        identity_maps=identity_count,
    )
