from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import api_router
from app.config import settings
from app.core.engine import EngineManager
from app.db.connection import init_db, close_db
from app.dependencies import init_services, set_engine
from app.providers.registry import register_deepsee_mock, register_defaults
from app.repositories import SQLiteContextRuntimeRepository
from app.services.audit_service import AuditService
from app.services.catalog_service import CatalogService
from app.services.identity_service import IdentityService
from app.services.provider_service import ProviderService
from app.services.refresh_scheduler import RefreshScheduler
from app.utils.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level)
    logger.info("Starting contextql-server")

    # Initialize durable state before the engine so executable DDL and
    # snapshots hydrate through the same repository used for writes.
    conn = init_db(settings.catalog_db)
    runtime_repository = SQLiteContextRuntimeRepository(conn)

    # Initialize engine
    manager = EngineManager(settings)
    engine = manager.initialize(
        catalog_repository=runtime_repository
    )

    if settings.register_mock_providers:
        register_defaults(engine)

    if settings.register_deepsee_mock:
        register_deepsee_mock(engine)

    # Initialize services
    audit = AuditService(conn)
    engine.on_ddl_audit(
        lambda event: audit.log(
            event_type=f"context.ddl.{event['action']}",
            resource_type="context",
            resource_name=event.get("context"),
            detail=event,
        )
    )
    catalog = CatalogService(conn, engine=engine, audit=audit)
    provider = ProviderService(conn, audit=audit)
    identity = IdentityService(conn, engine=engine, audit=audit)

    # Contexts and snapshots were hydrated by Engine construction.
    identity.sync_to_engine()

    # Wire dependencies
    init_services(engine, catalog, provider, identity, audit)

    scheduler = RefreshScheduler(
        engine,
        audit=audit,
        poll_seconds=settings.refresh_scheduler_poll_seconds,
    )
    if settings.refresh_scheduler_enabled:
        scheduler.start()

    logger.info(
        "Engine ready — tables=%s contexts=%s catalog_contexts=%d providers=%d identity_maps=%d",
        engine.catalog.tables(),
        engine.catalog.contexts(),
        catalog.list()[1],
        provider.list()[1],
        identity.list()[1],
    )

    audit.log("server_start", detail={"version": "0.3.0"})

    yield

    if settings.refresh_scheduler_enabled:
        scheduler.stop()
    audit.log("server_stop")
    close_db()
    logger.info("Shutting down contextql-server")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ContextQL Server",
        version="0.3.0",
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
