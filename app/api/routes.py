from fastapi import APIRouter

from app.api.audit import router as audit_router
from app.api.catalog import router as catalog_router
from app.api.explain import router as explain_router
from app.api.health import router as health_router
from app.api.identity import router as identity_router
from app.api.providers import router as providers_router
from app.api.query import router as query_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(query_router, tags=["query"])
api_router.include_router(explain_router)
api_router.include_router(catalog_router)
api_router.include_router(providers_router)
api_router.include_router(identity_router)
api_router.include_router(audit_router)
