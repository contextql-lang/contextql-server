from __future__ import annotations

import logging

import contextql as cql

from app.config import Settings

logger = logging.getLogger(__name__)


class EngineManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: cql.Engine | None = None

    def initialize(self) -> cql.Engine:
        if self._settings.use_demo:
            logger.info("Initializing demo engine")
            self._engine = cql.demo()
        else:
            logger.info("Initializing bare engine (database=%s)", self._settings.database)
            self._engine = cql.Engine(
                database=self._settings.database,
                mcp_timeout_ms=self._settings.mcp_timeout_ms,
                remote_timeout_ms=self._settings.remote_timeout_ms,
            )
        return self._engine

    @property
    def engine(self) -> cql.Engine:
        if self._engine is None:
            raise RuntimeError("Engine not initialized — call initialize() first")
        return self._engine
