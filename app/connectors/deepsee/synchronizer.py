"""Incremental synchronizer from DeepSee into a context membership store.

``DeepSeeSynchronizer`` bootstraps a context from a full snapshot and then
applies change events through the membership store's delta path
(plan 7.3 / DEEPSEE_CONNECTOR.md section 4):

- Deduplicate by ``event_id`` (idempotency key).
- Reject events at or before the committed ordering boundary
  (out-of-order or re-delivered history).
- Commit the new watermark only after snapshot promotion succeeds.

An incremental sync must leave the store equal to a clean rebuild from the
current source state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from contextql.membership import ContextMembershipStore, MembershipSnapshot

from app.connectors.deepsee.client import DeepSeeClient, parse_iso
from app.connectors.deepsee.models import (
    CHANGE_TYPE_ADDED,
    CHANGE_TYPE_REMOVED,
    CHANGE_TYPE_SCORE_CHANGED,
    ChangeEvent,
    SnapshotPage,
    contract_error,
)


@dataclass(frozen=True)
class SyncReport:
    """Outcome of a single :meth:`DeepSeeSynchronizer.sync_once` run."""

    applied_additions: int
    applied_removals: int
    applied_score_changes: int
    duplicates_skipped: int
    out_of_order_rejected: int
    committed_watermark: str

    @property
    def applied_total(self) -> int:
        return (self.applied_additions + self.applied_removals
                + self.applied_score_changes)


@dataclass(frozen=True)
class _FoldedChanges:
    """Net effect of an ordered event sequence."""

    additions: dict[int, float]
    removals: set[int]
    score_changes: dict[int, float]


class DeepSeeSynchronizer:
    """Keeps one context's membership in sync with DeepSee."""

    def __init__(
        self,
        client: DeepSeeClient,
        membership_store: ContextMembershipStore,
        context_id: str,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._store = membership_store
        self._context_id = context_id
        self._now = now if now is not None else _utc_now
        self._committed_watermark: str | None = None
        self._seen_event_ids: set[str] = set()

    @property
    def committed_watermark(self) -> str | None:
        """Watermark of the last successfully promoted snapshot."""
        return self._committed_watermark

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def bootstrap(self) -> MembershipSnapshot:
        """Ingest the full snapshot and commit its watermark."""
        first_page = self._client.fetch_snapshot()
        if first_page.membership_bitmap is not None:
            members: Iterable[int] = self._client.decode_membership_bitmap(
                first_page
            )
            scores = dict(first_page.scores)
            data_as_of = first_page.data_as_of
            watermark = first_page.watermark
        else:
            members, scores, data_as_of, watermark = \
                self._collect_id_pages(first_page)

        snapshot = self._store.put_snapshot(
            self._context_id,
            members,
            computed_at=self._now(),
            data_as_of=parse_iso(data_as_of),
            source_watermark=watermark,
            scores=scores,
        )
        # Commit only after successful promotion.
        self._committed_watermark = watermark
        return snapshot

    def _collect_id_pages(
        self, first_page: SnapshotPage
    ) -> tuple[list[int], dict[int, float], str, str]:
        members = list(first_page.entity_ids or ())
        scores = dict(first_page.scores)
        data_as_of = first_page.data_as_of
        watermark = first_page.watermark
        cursor = first_page.next_cursor
        while cursor is not None:
            page = self._client.fetch_snapshot(cursor=cursor)
            members.extend(page.entity_ids or ())
            scores.update(page.scores)
            data_as_of = page.data_as_of
            watermark = page.watermark
            cursor = page.next_cursor
        return members, scores, data_as_of, watermark

    # ------------------------------------------------------------------
    # Incremental sync
    # ------------------------------------------------------------------

    def sync_once(self) -> SyncReport:
        """Pull, deduplicate, order-check, and apply pending changes."""
        if self._committed_watermark is None:
            raise contract_error(
                "Synchronizer requires bootstrap() before sync_once()."
            )
        events, data_as_of = self._fetch_all_changes()
        applicable, duplicates, out_of_order = self._filter_events(events)
        if not applicable:
            return SyncReport(
                applied_additions=0,
                applied_removals=0,
                applied_score_changes=0,
                duplicates_skipped=duplicates,
                out_of_order_rejected=out_of_order,
                committed_watermark=self._committed_watermark,
            )

        ordered = sorted(applicable, key=lambda e: int(e.watermark))
        folded = _fold_events(ordered)
        new_watermark = ordered[-1].watermark

        self._store.apply_delta(
            self._context_id,
            additions=sorted(folded.additions),
            removals=sorted(folded.removals),
            score_changes={**folded.additions, **folded.score_changes},
            computed_at=self._now(),
            data_as_of=parse_iso(data_as_of),
            source_watermark=new_watermark,
        )
        # Commit watermark and idempotency keys only after promotion.
        self._committed_watermark = new_watermark
        self._seen_event_ids.update(e.event_id for e in ordered)
        return SyncReport(
            applied_additions=len(folded.additions),
            applied_removals=len(folded.removals),
            applied_score_changes=len(folded.score_changes),
            duplicates_skipped=duplicates,
            out_of_order_rejected=out_of_order,
            committed_watermark=new_watermark,
        )

    def _fetch_all_changes(self) -> tuple[list[ChangeEvent], str]:
        assert self._committed_watermark is not None
        events: list[ChangeEvent] = []
        cursor: str | None = None
        while True:
            batch = self._client.fetch_changes(
                self._committed_watermark, cursor=cursor
            )
            events.extend(batch.events)
            if batch.next_cursor is None:
                return events, batch.data_as_of
            cursor = batch.next_cursor

    def _filter_events(
        self, events: list[ChangeEvent]
    ) -> tuple[list[ChangeEvent], int, int]:
        boundary = int(self._committed_watermark or "0")
        applicable: list[ChangeEvent] = []
        batch_event_ids: set[str] = set()
        duplicates = 0
        out_of_order = 0
        for event in events:
            if event.event_id in self._seen_event_ids or \
                    event.event_id in batch_event_ids:
                duplicates += 1
            elif int(event.watermark) <= boundary:
                out_of_order += 1
            else:
                batch_event_ids.add(event.event_id)
                applicable.append(event)
        return applicable, duplicates, out_of_order


def _fold_events(ordered: list[ChangeEvent]) -> _FoldedChanges:
    """Fold an ordered event sequence into its net delta."""
    additions: dict[int, float] = {}
    removals: set[int] = set()
    score_changes: dict[int, float] = {}
    for event in ordered:
        entity_id = int(event.entity_id)
        if event.change_type == CHANGE_TYPE_ADDED:
            additions[entity_id] = float(event.score or 1.0)
            removals.discard(entity_id)
        elif event.change_type == CHANGE_TYPE_REMOVED:
            removals.add(entity_id)
            additions.pop(entity_id, None)
            score_changes.pop(entity_id, None)
        elif event.change_type == CHANGE_TYPE_SCORE_CHANGED:
            if event.score is None:
                raise contract_error(
                    f"score_changed event {event.event_id} carries no score."
                )
            if entity_id in additions:
                additions[entity_id] = float(event.score)
            else:
                score_changes[entity_id] = float(event.score)
    return _FoldedChanges(
        additions=additions, removals=removals, score_changes=score_changes
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
