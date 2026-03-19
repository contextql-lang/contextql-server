import pytest
from fastapi.testclient import TestClient

import contextql as cql

from app.dependencies import set_engine
from app.main import create_app
from app.providers.registry import register_defaults


@pytest.fixture(scope="session")
def engine():
    eng = cql.demo()
    register_defaults(eng)
    return eng


@pytest.fixture(scope="session")
def client(engine):
    set_engine(engine)
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc
