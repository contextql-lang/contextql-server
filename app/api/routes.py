from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.query import router as query_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(query_router, tags=["query"])
