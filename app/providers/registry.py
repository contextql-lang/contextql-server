from __future__ import annotations

import logging

import contextql as cql

from app.providers.mock import EchoMCPProvider, StaticRemoteProvider

logger = logging.getLogger(__name__)


def register_defaults(engine: cql.Engine) -> None:
    # Built-in reference providers from contextql
    engine.register_mcp_provider("fraud_detection", cql.FraudDetectionMCP())
    engine.register_mcp_provider("priority", cql.PriorityMCP())
    logger.info("Registered MCP providers: fraud_detection, priority")

    engine.register_remote_provider("jira", cql.JiraRemoteProvider())
    logger.info("Registered REMOTE providers: jira")

    # Mock providers for demo/testing
    engine.register_mcp_provider("echo", EchoMCPProvider())
    logger.info("Registered mock MCP provider: echo")

    engine.register_remote_provider("static", StaticRemoteProvider())
    logger.info("Registered mock REMOTE provider: static")
