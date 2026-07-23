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


def register_deepsee_mock(engine: cql.Engine) -> None:
    """Register the mock DeepSee connector providers (plan 8.4).

    Guarded by the ``CQL_REGISTER_DEEPSEE_MOCK`` config flag (default off).
    Provider names follow the planned registration: the MCP role serves
    ``deepsee_settlement_risk`` membership; the REMOTE role serves the
    ``settlement_cases`` and ``reconciliation_evidence`` resources.
    """
    from app.connectors.deepsee import (
        DeepSeeClient,
        DeepSeeMCPProvider,
        DeepSeeRemoteProvider,
        MockDeepSeeService,
    )

    service = MockDeepSeeService()
    client = DeepSeeClient(service)
    engine.register_mcp_provider(
        "deepsee_settlement_risk", DeepSeeMCPProvider(client)
    )
    logger.info("Registered mock MCP provider: deepsee_settlement_risk")

    engine.register_remote_provider("deepsee", DeepSeeRemoteProvider(client))
    logger.info("Registered mock REMOTE provider: deepsee")
