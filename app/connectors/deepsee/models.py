"""Wire-contract models and error types for the mock DeepSee connector.

These dataclasses define the executable contract between ContextQL and a
future DeepSee endpoint (plan section 8.3, DEEPSEE_CONNECTOR.md section 5).
Nothing here describes DeepSee's actual API — the shapes exist so the
client, providers, and synchronizer can be exercised end to end before the
discovery gate (plan 8.5) is satisfied.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Supported entity key type for the first proof (plan design decision 11).
SUPPORTED_ENTITY_KEY_TYPE = "INT64"

#: Supported serialized bitmap encoding (pyroaring ``BitMap64.serialize``).
SUPPORTED_BITMAP_ENCODING = "roaring64"

#: Change types a :class:`ChangeEvent` may carry.
CHANGE_TYPE_ADDED = "added"
CHANGE_TYPE_REMOVED = "removed"
CHANGE_TYPE_SCORE_CHANGED = "score_changed"
VALID_CHANGE_TYPES = frozenset(
    {CHANGE_TYPE_ADDED, CHANGE_TYPE_REMOVED, CHANGE_TYPE_SCORE_CHANGED}
)


@dataclass(frozen=True)
class ChangeEvent:
    """A single membership change delivered by the change feed."""

    event_id: str
    entity_id: int
    change_type: str
    score: float | None
    effective_at: str
    watermark: str

    def __post_init__(self) -> None:
        if self.change_type not in VALID_CHANGE_TYPES:
            raise ValueError(
                f"Invalid change_type {self.change_type!r}; expected one of "
                f"{sorted(VALID_CHANGE_TYPES)}."
            )


@dataclass(frozen=True)
class SnapshotPage:
    """One page of a full membership snapshot.

    Membership arrives either as ``entity_ids`` (paginated) or as a single
    serialized ``membership_bitmap`` page — never both.
    """

    entity_key_type: str
    data_as_of: str
    watermark: str
    entity_ids: tuple[int, ...] | None = None
    membership_bitmap: bytes | None = None
    bitmap_encoding: str | None = None
    scores: dict[int, float] = field(default_factory=dict)
    evidence_refs: dict[int, str] = field(default_factory=dict)
    next_cursor: str | None = None


@dataclass(frozen=True)
class ChangeBatch:
    """One page of change events since a watermark."""

    events: tuple[ChangeEvent, ...]
    data_as_of: str
    watermark: str
    next_cursor: str | None = None


@dataclass(frozen=True)
class CasesResponse:
    """Relational evidence rows for a REMOTE resource."""

    resource: str
    rows: tuple[dict, ...]
    schema: dict[str, str]
    data_as_of: str
    watermark: str
    next_cursor: str | None = None


@dataclass(frozen=True)
class ErrorEnvelope:
    """Structured error payload the mock service attaches to failures."""

    code: str
    message: str
    retryable: bool
    retry_after_ms: int | None = None


class DeepSeeError(Exception):
    """Base class for all connector errors."""

    def __init__(self, envelope: ErrorEnvelope) -> None:
        super().__init__(f"[{envelope.code}] {envelope.message}")
        self.envelope = envelope


class DeepSeeRetryableError(DeepSeeError):
    """Transient failure (503-style); safe to retry with backoff."""


class DeepSeeTimeoutError(DeepSeeRetryableError):
    """Simulated request timeout; treated as retryable."""


class DeepSeeRateLimitError(DeepSeeRetryableError):
    """Rate limit hit; retryable after ``envelope.retry_after_ms``."""


class DeepSeeTerminalError(DeepSeeError):
    """Non-retryable failure; must propagate to the caller."""


class DeepSeeAuthError(DeepSeeTerminalError):
    """Authentication or authorization failure; never retried."""


class DeepSeeContractError(DeepSeeTerminalError):
    """The response violates the wire contract (key type, payload, ...)."""


class DeepSeeStaleDataError(DeepSeeContractError):
    """``data_as_of`` is older than the configured staleness bound."""


def contract_error(message: str) -> DeepSeeContractError:
    """Build a :class:`DeepSeeContractError` with a standard envelope."""
    return DeepSeeContractError(
        ErrorEnvelope(code="CONTRACT_VIOLATION", message=message, retryable=False)
    )
