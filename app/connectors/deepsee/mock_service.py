"""In-process deterministic stand-in for a DeepSee endpoint.

``MockDeepSeeService`` holds settlement-risk membership (integer transaction
IDs plus scores) and relational evidence rows, and exposes the three wire
operations the connector needs: full snapshot, change feed, and case/evidence
queries. Configurable behaviors exercise every mock-contract case from plan
section 8.3 (pagination, duplicates, out-of-order delivery, retryable and
terminal errors, auth failure, timeout, rate limiting, stale data, invalid
key types, malformed bitmap payloads).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone

from app.connectors.deepsee.models import (
    CHANGE_TYPE_ADDED,
    CHANGE_TYPE_REMOVED,
    CHANGE_TYPE_SCORE_CHANGED,
    SUPPORTED_BITMAP_ENCODING,
    SUPPORTED_ENTITY_KEY_TYPE,
    CasesResponse,
    ChangeBatch,
    ChangeEvent,
    DeepSeeAuthError,
    DeepSeeRateLimitError,
    DeepSeeRetryableError,
    DeepSeeTerminalError,
    DeepSeeTimeoutError,
    ErrorEnvelope,
    SnapshotPage,
)

#: Failure kinds accepted by :meth:`MockDeepSeeService.queue_failure`.
FAILURE_RETRYABLE = "retryable"
FAILURE_TERMINAL = "terminal"
FAILURE_AUTH = "auth"
FAILURE_TIMEOUT = "timeout"
FAILURE_RATE_LIMIT = "rate_limit"
_VALID_FAILURES = frozenset(
    {FAILURE_RETRYABLE, FAILURE_TERMINAL, FAILURE_AUTH,
     FAILURE_TIMEOUT, FAILURE_RATE_LIMIT}
)

_BASE_TIME = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
_STALE_DATA_AS_OF = "2000-01-01T00:00:00+00:00"
_RETRY_AFTER_MS = 100

_BREAK_TYPES = ("quantity_mismatch", "price_mismatch",
                "missing_confirmation", "ssi_mismatch")
_ACTIONS = ("amend_and_resubmit", "affirm_with_counterparty",
            "escalate_to_desk", "cancel_and_rebook")
_OWNERS = ("ops-alice", "ops-bob", "ops-carol")
_CASE_STATUSES = ("open", "investigating", "resolved")

RESOURCE_SETTLEMENT_CASES = "settlement_cases"
RESOURCE_RECONCILIATION_EVIDENCE = "reconciliation_evidence"
VALID_RESOURCES = frozenset(
    {RESOURCE_SETTLEMENT_CASES, RESOURCE_RECONCILIATION_EVIDENCE}
)

_CASE_SCHEMA: dict[str, str] = {
    "transaction_id": "INT64",
    "break_type": "VARCHAR",
    "recommended_action": "VARCHAR",
    "agent_decision": "VARCHAR",
    "confidence": "DOUBLE",
    "explanation": "VARCHAR",
    "evidence_ref": "VARCHAR",
    "case_status": "VARCHAR",
    "owner": "VARCHAR",
    "last_reviewed_at": "VARCHAR",
}


def default_membership(count: int = 40, start_id: int = 1000) -> dict[int, float]:
    """Deterministic default settlement-risk membership (id -> score)."""
    return {
        start_id + offset: round(0.50 + (offset % 50) / 100.0, 4)
        for offset in range(count)
    }


class MockDeepSeeService:
    """Deterministic in-process mock of the DeepSee wire contract."""

    def __init__(
        self,
        *,
        members: dict[int, float] | None = None,
        page_size: int = 100,
        require_auth: bool = False,
        bitmap_snapshot: bool = False,
        malformed_bitmap: bool = False,
        invalid_key_type: bool = False,
        stale_data_as_of: bool = False,
        duplicate_delivery: bool = False,
        out_of_order: bool = False,
        redeliver_from_start: bool = False,
    ) -> None:
        initial = dict(members) if members is not None else default_membership()
        self._members: dict[int, float] = {
            int(k): float(v) for k, v in initial.items()
        }
        self.page_size = int(page_size)
        self.require_auth = require_auth
        self.bitmap_snapshot = bitmap_snapshot
        self.malformed_bitmap = malformed_bitmap
        self.invalid_key_type = invalid_key_type
        self.stale_data_as_of = stale_data_as_of
        self.duplicate_delivery = duplicate_delivery
        self.out_of_order = out_of_order
        self.redeliver_from_start = redeliver_from_start
        self._events: list[ChangeEvent] = []
        self._seq = 0
        self._failures: deque[str] = deque()
        self.call_count = 0

    # ------------------------------------------------------------------
    # Behavior configuration
    # ------------------------------------------------------------------

    def queue_failure(self, kind: str, count: int = 1) -> None:
        """Make the next *count* API calls fail with the given kind."""
        if kind not in _VALID_FAILURES:
            raise ValueError(
                f"Unknown failure kind {kind!r}; expected one of "
                f"{sorted(_VALID_FAILURES)}."
            )
        self._failures.extend([kind] * count)

    # ------------------------------------------------------------------
    # World mutation (produces change events)
    # ------------------------------------------------------------------

    def add_member(self, entity_id: int, score: float) -> ChangeEvent:
        """Add a transaction to the membership and emit an event."""
        entity_id = int(entity_id)
        self._members[entity_id] = float(score)
        return self._record_event(entity_id, CHANGE_TYPE_ADDED, float(score))

    def remove_member(self, entity_id: int) -> ChangeEvent:
        """Remove a transaction from the membership and emit an event."""
        entity_id = int(entity_id)
        self._members.pop(entity_id, None)
        return self._record_event(entity_id, CHANGE_TYPE_REMOVED, None)

    def change_score(self, entity_id: int, score: float) -> ChangeEvent:
        """Change a member's score and emit an event."""
        entity_id = int(entity_id)
        if entity_id not in self._members:
            raise ValueError(f"Entity {entity_id} is not a current member.")
        self._members[entity_id] = float(score)
        return self._record_event(
            entity_id, CHANGE_TYPE_SCORE_CHANGED, float(score)
        )

    # ------------------------------------------------------------------
    # Wire operations
    # ------------------------------------------------------------------

    def snapshot(
        self, cursor: str | None = None, token: str | None = None
    ) -> SnapshotPage:
        """Return one page of the current full membership snapshot."""
        self._enter_call(token)
        if self.bitmap_snapshot:
            return self._bitmap_snapshot_page()
        ordered = sorted(self._members)
        offset = int(cursor) if cursor is not None else 0
        page_ids = ordered[offset:offset + self.page_size]
        has_more = offset + self.page_size < len(ordered)
        return SnapshotPage(
            entity_key_type=self._entity_key_type(),
            data_as_of=self._data_as_of(),
            watermark=self._watermark(),
            entity_ids=tuple(page_ids),
            scores={i: self._members[i] for i in page_ids},
            evidence_refs={i: _evidence_ref(i) for i in page_ids},
            next_cursor=str(offset + self.page_size) if has_more else None,
        )

    def changes(
        self,
        since_watermark: str,
        cursor: str | None = None,
        token: str | None = None,
    ) -> ChangeBatch:
        """Return one page of change events after *since_watermark*."""
        self._enter_call(token)
        since = int(since_watermark)
        if self.redeliver_from_start:
            pending = list(self._events)
        else:
            pending = [e for e in self._events if int(e.watermark) > since]
        if self.out_of_order:
            pending = list(reversed(pending))
        if self.duplicate_delivery:
            pending = [event for event in pending for _ in (0, 1)]
        offset = int(cursor) if cursor is not None else 0
        page = pending[offset:offset + self.page_size]
        has_more = offset + self.page_size < len(pending)
        return ChangeBatch(
            events=tuple(page),
            data_as_of=self._data_as_of(),
            watermark=self._watermark(),
            next_cursor=str(offset + self.page_size) if has_more else None,
        )

    def cases(
        self,
        resource: str,
        filters: dict | None = None,
        columns: list[str] | None = None,
        limit: int | None = None,
        token: str | None = None,
    ) -> CasesResponse:
        """Return evidence rows for one of the two supported resources."""
        self._enter_call(token)
        if resource not in VALID_RESOURCES:
            raise DeepSeeTerminalError(ErrorEnvelope(
                code="UNKNOWN_RESOURCE",
                message=(
                    f"Unknown resource {resource!r}; expected one of "
                    f"{sorted(VALID_RESOURCES)}."
                ),
                retryable=False,
            ))
        rows = [
            _case_row(resource, entity_id, score)
            for entity_id, score in sorted(self._members.items())
        ]
        for key, value in (filters or {}).items():
            rows = [row for row in rows if row.get(key) == value]
        if columns:
            rows = [{c: row.get(c) for c in columns} for row in rows]
        if limit is not None:
            rows = rows[:limit]
        schema = {
            c: t for c, t in _CASE_SCHEMA.items()
            if not columns or c in columns
        }
        return CasesResponse(
            resource=resource,
            rows=tuple(rows),
            schema=schema,
            data_as_of=self._data_as_of(),
            watermark=self._watermark(),
        )

    # ------------------------------------------------------------------
    # Introspection helpers (used by tests)
    # ------------------------------------------------------------------

    def current_members(self) -> dict[int, float]:
        """Copy of the current membership (id -> score)."""
        return dict(self._members)

    def committed_watermark(self) -> str:
        """The watermark covering all emitted events so far."""
        return self._watermark()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _enter_call(self, token: str | None) -> None:
        self.call_count += 1
        if self._failures:
            _raise_failure(self._failures.popleft())
        if self.require_auth and not token:
            raise DeepSeeAuthError(ErrorEnvelope(
                code="AUTH_FAILED",
                message="Missing or invalid DeepSee credential.",
                retryable=False,
            ))

    def _record_event(
        self, entity_id: int, change_type: str, score: float | None
    ) -> ChangeEvent:
        self._seq += 1
        event = ChangeEvent(
            event_id=f"evt-{self._seq:08d}",
            entity_id=entity_id,
            change_type=change_type,
            score=score,
            effective_at=(
                _BASE_TIME + timedelta(seconds=self._seq)
            ).isoformat(),
            watermark=f"{self._seq:012d}",
        )
        self._events.append(event)
        return event

    def _watermark(self) -> str:
        return f"{self._seq:012d}"

    def _data_as_of(self) -> str:
        if self.stale_data_as_of:
            return _STALE_DATA_AS_OF
        return (_BASE_TIME + timedelta(seconds=self._seq)).isoformat()

    def _entity_key_type(self) -> str:
        return "STRING" if self.invalid_key_type else SUPPORTED_ENTITY_KEY_TYPE

    def _bitmap_snapshot_page(self) -> SnapshotPage:
        if self.malformed_bitmap:
            payload = b"this-is-not-a-roaring64-bitmap"
        else:
            from pyroaring import BitMap64

            payload = BitMap64(self._members.keys()).serialize()
        return SnapshotPage(
            entity_key_type=self._entity_key_type(),
            data_as_of=self._data_as_of(),
            watermark=self._watermark(),
            membership_bitmap=payload,
            bitmap_encoding=SUPPORTED_BITMAP_ENCODING,
            scores=dict(self._members),
            evidence_refs={i: _evidence_ref(i) for i in self._members},
            next_cursor=None,
        )


def _evidence_ref(entity_id: int) -> str:
    return f"deepsee://case/{entity_id}"


def _case_row(resource: str, entity_id: int, score: float) -> dict:
    """Deterministic evidence row derived from the entity ID."""
    decided = entity_id % 2 == 0
    reviewed_at = (_BASE_TIME + timedelta(minutes=entity_id % 60)).isoformat()
    prefix = "Reconciliation" if resource == RESOURCE_RECONCILIATION_EVIDENCE \
        else "Settlement"
    return {
        "transaction_id": entity_id,
        "break_type": _BREAK_TYPES[entity_id % len(_BREAK_TYPES)],
        "recommended_action": _ACTIONS[entity_id % len(_ACTIONS)],
        "agent_decision": "auto_resolve" if decided else "route_to_human",
        "confidence": score,
        "explanation": (
            f"{prefix} break on transaction {entity_id}: "
            f"{_BREAK_TYPES[entity_id % len(_BREAK_TYPES)]} detected."
        ),
        "evidence_ref": _evidence_ref(entity_id),
        "case_status": _CASE_STATUSES[entity_id % len(_CASE_STATUSES)],
        "owner": _OWNERS[entity_id % len(_OWNERS)],
        "last_reviewed_at": reviewed_at,
    }


def _raise_failure(kind: str) -> None:
    if kind == FAILURE_RETRYABLE:
        raise DeepSeeRetryableError(ErrorEnvelope(
            code="SERVICE_UNAVAILABLE",
            message="DeepSee service temporarily unavailable (503).",
            retryable=True,
        ))
    if kind == FAILURE_TIMEOUT:
        raise DeepSeeTimeoutError(ErrorEnvelope(
            code="TIMEOUT",
            message="DeepSee request timed out.",
            retryable=True,
        ))
    if kind == FAILURE_RATE_LIMIT:
        raise DeepSeeRateLimitError(ErrorEnvelope(
            code="RATE_LIMITED",
            message="DeepSee rate limit exceeded.",
            retryable=True,
            retry_after_ms=_RETRY_AFTER_MS,
        ))
    if kind == FAILURE_AUTH:
        raise DeepSeeAuthError(ErrorEnvelope(
            code="AUTH_FAILED",
            message="DeepSee rejected the supplied credential.",
            retryable=False,
        ))
    raise DeepSeeTerminalError(ErrorEnvelope(
        code="INTERNAL_ERROR",
        message="DeepSee reported an unrecoverable internal error.",
        retryable=False,
    ))
