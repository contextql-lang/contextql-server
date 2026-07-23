"""Mock DeepSee connector (executable contract until the discovery gate).

Public surface:

- :class:`MockDeepSeeService` — deterministic in-process stand-in.
- :class:`DeepSeeClient` — retrying, validating wire client.
- :class:`DeepSeeMCPProvider` — membership + scores (MCP role).
- :class:`DeepSeeRemoteProvider` — relational evidence (REMOTE role).
- :class:`DeepSeeSynchronizer` — incremental membership synchronization.
- :func:`resolve_credential` — credential-reference resolution.
"""
from app.connectors.deepsee.auth import (
    CredentialResolutionError,
    resolve_credential,
)
from app.connectors.deepsee.client import DeepSeeClient
from app.connectors.deepsee.mcp_provider import DeepSeeMCPProvider
from app.connectors.deepsee.mock_service import MockDeepSeeService
from app.connectors.deepsee.models import (
    CasesResponse,
    ChangeBatch,
    ChangeEvent,
    DeepSeeAuthError,
    DeepSeeContractError,
    DeepSeeError,
    DeepSeeRateLimitError,
    DeepSeeRetryableError,
    DeepSeeStaleDataError,
    DeepSeeTerminalError,
    DeepSeeTimeoutError,
    ErrorEnvelope,
    SnapshotPage,
)
from app.connectors.deepsee.remote_provider import DeepSeeRemoteProvider
from app.connectors.deepsee.synchronizer import DeepSeeSynchronizer, SyncReport

__all__ = [
    "CasesResponse",
    "ChangeBatch",
    "ChangeEvent",
    "CredentialResolutionError",
    "DeepSeeAuthError",
    "DeepSeeClient",
    "DeepSeeContractError",
    "DeepSeeError",
    "DeepSeeMCPProvider",
    "DeepSeeRateLimitError",
    "DeepSeeRemoteProvider",
    "DeepSeeRetryableError",
    "DeepSeeStaleDataError",
    "DeepSeeSynchronizer",
    "DeepSeeTerminalError",
    "DeepSeeTimeoutError",
    "ErrorEnvelope",
    "MockDeepSeeService",
    "SnapshotPage",
    "SyncReport",
    "resolve_credential",
]
