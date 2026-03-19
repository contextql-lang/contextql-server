from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import api_router
from app.config import settings
from app.core.engine import EngineManager
from app.dependencies import set_engine
from app.providers.registry import register_defaults
from app.utils.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level)
    logger.info("Starting contextql-server")

    manager = EngineManager(settings)
    engine = manager.initialize()

    if settings.register_mock_providers:
        register_defaults(engine)

    set_engine(engine)
    logger.info("Engine ready — tables=%s contexts=%s",
                engine.catalog.tables(), engine.catalog.contexts())

    yield

    logger.info("Shutting down contextql-server")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ContextQL Server",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(api_router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
