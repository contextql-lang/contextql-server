import pytest
from fastapi.testclient import TestClient

import contextql as cql

from app.db.connection import init_db, close_db
from app.dependencies import init_services, set_engine
from app.main import create_app
from app.providers.registry import register_defaults
from app.services.audit_service import AuditService
from app.services.catalog_service import CatalogService
from app.services.identity_service import IdentityService
from app.services.provider_service import ProviderService


@pytest.fixture(scope="session")
def engine():
    eng = cql.demo()
    register_defaults(eng)
    return eng


@pytest.fixture(scope="session")
def db_conn():
    conn = init_db(":memory:")
    yield conn
    close_db()


@pytest.fixture(scope="session")
def services(engine, db_conn):
    audit = AuditService(db_conn)
    catalog = CatalogService(db_conn, engine=engine, audit=audit)
    provider = ProviderService(db_conn, audit=audit)
    identity = IdentityService(db_conn, engine=engine, audit=audit)
    init_services(engine, catalog, provider, identity, audit)
    return {
        "audit": audit,
        "catalog": catalog,
        "provider": provider,
        "identity": identity,
    }


@pytest.fixture(scope="session")
def client(engine, services):
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc
