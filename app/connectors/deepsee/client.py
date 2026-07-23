"""Client wrapper around the (mock) DeepSee service.

``DeepSeeClient`` owns transport-level concerns: credential resolution per
request, retry with capped exponential backoff for retryable failures,
payload validation before any bitmap decode, and contract checks (entity
key type, staleness bounds). Terminal errors propagate unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

from contextql.providers import EntityFilter

from contextql.providers.base import MAX_BITMAP_PAYLOAD_BYTES

from app.connectors.deepsee.auth import resolve_credential
from app.connectors.deepsee.mock_service import MockDeepSeeService
from app.connectors.deepsee.models import (
    SUPPORTED_BITMAP_ENCODING,
    SUPPORTED_ENTITY_KEY_TYPE,
    CasesResponse,
    ChangeBatch,
    DeepSeeRateLimitError,
    DeepSeeRetryableError,
    DeepSeeStaleDataError,
    ErrorEnvelope,
    SnapshotPage,
    contract_error,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_MS = 50
DEFAULT_BACKOFF_CAP_MS = 1000


class DeepSeeClient:
    """Retrying, validating client for the DeepSee wire contract.

    Args:
        service: The service to call (the in-process mock for now).
        credential_reference: Credential reference (e.g.
            ``"env:CQL_DEEPSEE_TOKEN"``) resolved per request. ``None``
            skips authentication (the mock default).
        max_retries: Retries after the initial attempt for retryable errors.
        backoff_base_ms / backoff_cap_ms: Exponential backoff parameters.
        sleeper: Injectable ``sleep(seconds)`` callable; tests inject a
            recorder so no real sleeping occurs.
        max_payload_bytes: Serialized bitmap payloads larger than this are
            rejected before any decode attempt.
        max_data_age: When set, responses whose ``data_as_of`` is older than
            this bound (relative to ``now()``) raise
            :class:`DeepSeeStaleDataError`.
        now: Injectable clock returning an aware ``datetime``.
    """

    def __init__(
        self,
        service: MockDeepSeeService,
        *,
        credential_reference: str | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_ms: int = DEFAULT_BACKOFF_BASE_MS,
        backoff_cap_ms: int = DEFAULT_BACKOFF_CAP_MS,
        sleeper: Callable[[float], None] | None = None,
        max_payload_bytes: int = MAX_BITMAP_PAYLOAD_BYTES,
        max_data_age: timedelta | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._service = service
        self._credential_reference = credential_reference
        self._max_retries = max(0, int(max_retries))
        self._backoff_base_ms = int(backoff_base_ms)
        self._backoff_cap_ms = int(backoff_cap_ms)
        self._sleeper = sleeper if sleeper is not None else _default_sleep
        self._max_payload_bytes = int(max_payload_bytes)
        self._max_data_age = max_data_age
        self._now = now if now is not None else _utc_now

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def fetch_snapshot(self, cursor: str | None = None) -> SnapshotPage:
        """Fetch one validated page of the full membership snapshot."""
        token = self._token()
        page = self._call_with_retry(
            lambda: self._service.snapshot(cursor=cursor, token=token)
        )
        self._validate_freshness(page.data_as_of)
        self._validate_key_type(page.entity_key_type)
        if page.membership_bitmap is not None:
            self._validate_bitmap_payload(
                page.membership_bitmap, page.bitmap_encoding
            )
        else:
            self._validate_entity_ids(page.entity_ids)
        return page

    def fetch_changes(
        self, since_watermark: str, cursor: str | None = None
    ) -> ChangeBatch:
        """Fetch one page of change events after *since_watermark*."""
        token = self._token()
        batch = self._call_with_retry(
            lambda: self._service.changes(
                since_watermark, cursor=cursor, token=token
            )
        )
        self._validate_freshness(batch.data_as_of)
        return batch

    def fetch_cases(
        self,
        resource: str,
        filters: dict | None = None,
        columns: list[str] | None = None,
        limit: int | None = None,
        entity_filter: EntityFilter | None = None,
    ) -> CasesResponse:
        """Fetch evidence rows for a REMOTE resource."""
        token = self._token()
        call_kwargs = {
            "filters": filters,
            "columns": columns,
            "limit": limit,
            "token": token,
        }
        if entity_filter is not None:
            call_kwargs["entity_filter"] = entity_filter
        response = self._call_with_retry(
            lambda: self._service.cases(resource, **call_kwargs)
        )
        self._validate_freshness(response.data_as_of)
        return response

    def decode_membership_bitmap(self, page: SnapshotPage) -> "BitMap64":
        """Decode a validated bitmap page without expanding it to a list."""
        payload = page.membership_bitmap
        if payload is None:
            raise contract_error("Snapshot page carries no bitmap payload.")
        self._validate_bitmap_payload(payload, page.bitmap_encoding)
        from pyroaring import BitMap64

        try:
            return BitMap64.deserialize(payload)
        except Exception as exc:
            raise contract_error(
                f"Malformed roaring64 bitmap payload: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Retry / validation internals
    # ------------------------------------------------------------------

    def _token(self) -> str | None:
        if self._credential_reference is None:
            return None
        return resolve_credential(self._credential_reference)

    def _call_with_retry(self, operation: Callable[[], T]) -> T:
        attempt = 0
        while True:
            try:
                return operation()
            except DeepSeeRetryableError as exc:
                if attempt >= self._max_retries:
                    raise
                self._sleeper(self._retry_delay_seconds(exc, attempt))
                attempt += 1

    def _retry_delay_seconds(
        self, exc: DeepSeeRetryableError, attempt: int
    ) -> float:
        if isinstance(exc, DeepSeeRateLimitError) and \
                exc.envelope.retry_after_ms is not None:
            delay_ms = float(exc.envelope.retry_after_ms)
        else:
            delay_ms = float(self._backoff_base_ms * (2 ** attempt))
        return min(delay_ms, float(self._backoff_cap_ms)) / 1000.0

    def _validate_bitmap_payload(
        self, payload: bytes, encoding: str | None
    ) -> None:
        if len(payload) > self._max_payload_bytes:
            raise contract_error(
                f"Membership bitmap payload of {len(payload)} bytes exceeds "
                f"the {self._max_payload_bytes}-byte limit; refusing to "
                "decode."
            )
        if encoding != SUPPORTED_BITMAP_ENCODING:
            raise contract_error(
                f"Unsupported bitmap encoding {encoding!r}; expected "
                f"{SUPPORTED_BITMAP_ENCODING!r}."
            )

    def _validate_key_type(self, entity_key_type: str) -> None:
        if entity_key_type != SUPPORTED_ENTITY_KEY_TYPE:
            raise contract_error(
                f"Unsupported entity key type {entity_key_type!r}; only "
                f"{SUPPORTED_ENTITY_KEY_TYPE!r} keys are supported "
                "(plan design decision 11)."
            )

    def _validate_entity_ids(self, entity_ids: tuple | None) -> None:
        if entity_ids is None:
            raise contract_error(
                "Snapshot page carries neither entity_ids nor a bitmap."
            )
        for entity_id in entity_ids:
            if not isinstance(entity_id, int) or entity_id < 0:
                raise contract_error(
                    f"Entity ID {entity_id!r} is not a non-negative "
                    "integer; only INT64 keys are supported."
                )

    def _validate_freshness(self, data_as_of: str) -> None:
        if self._max_data_age is None:
            return
        as_of = parse_iso(data_as_of)
        age = self._now() - as_of
        if age > self._max_data_age:
            raise DeepSeeStaleDataError(ErrorEnvelope(
                code="STALE_DATA",
                message=(
                    f"data_as_of {data_as_of} is {age} old, exceeding the "
                    f"configured staleness bound of {self._max_data_age}."
                ),
                retryable=False,
            ))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
