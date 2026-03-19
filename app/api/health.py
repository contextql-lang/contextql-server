from fastapi import APIRouter, Depends

import contextql as cql

from app.dependencies import get_engine
from app.models.response import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(engine: cql.Engine = Depends(get_engine)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        engine_version=cql.__version__,
        tables=engine.catalog.tables(),
        contexts=engine.catalog.contexts(),
    )
