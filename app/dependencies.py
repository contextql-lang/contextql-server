from __future__ import annotations

import contextql as cql

from app.services.query_service import QueryService

_engine: cql.Engine | None = None
_query_service: QueryService | None = None


def set_engine(engine: cql.Engine) -> None:
    global _engine, _query_service
    _engine = engine
    _query_service = QueryService(engine)


def get_engine() -> cql.Engine:
    if _engine is None:
        raise RuntimeError("Engine not initialized")
    return _engine


def get_query_service() -> QueryService:
    if _query_service is None:
        raise RuntimeError("QueryService not initialized")
    return _query_service
