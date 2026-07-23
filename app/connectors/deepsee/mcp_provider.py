"""DeepSee MCP provider: settlement-risk membership and scores.

Implements the engine's ``MCPProvider`` protocol. Small results return an
explicit entity ID list; results at or above ``bitmap_threshold`` return a
serialized roaring64 bitmap (plan 8.1/8.2). A bitmap received from the wire
is passed through in serialized form — it is never expanded into a Python
list inside the provider.
"""
from __future__ import annotations

from contextql.providers import MCPResult

from app.connectors.deepsee.client import DeepSeeClient
from app.connectors.deepsee.models import (
    SUPPORTED_BITMAP_ENCODING,
    SUPPORTED_ENTITY_KEY_TYPE,
    SnapshotPage,
)

DEFAULT_BITMAP_THRESHOLD = 1000


class DeepSeeMCPProvider:
    """MCP provider backed by a :class:`DeepSeeClient`."""

    def __init__(
        self,
        client: DeepSeeClient,
        *,
        bitmap_threshold: int = DEFAULT_BITMAP_THRESHOLD,
    ) -> None:
        self._client = client
        self._bitmap_threshold = int(bitmap_threshold)

    def resolve(
        self,
        entity_type: str,
        params: dict,
        limit: int | None = None,
    ) -> MCPResult:
        """Resolve current settlement-risk membership for *entity_type*."""
        first_page = self._client.fetch_snapshot()
        if first_page.membership_bitmap is not None:
            return self._bitmap_result(entity_type, first_page)
        return self._id_list_result(entity_type, first_page, limit)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _bitmap_result(
        self, entity_type: str, page: SnapshotPage
    ) -> MCPResult:
        """Pass a wire bitmap through without decoding it (plan 8.2)."""
        return MCPResult(
            entity_type=entity_type,
            membership_bitmap=page.membership_bitmap,
            bitmap_encoding=page.bitmap_encoding,
            scores=dict(page.scores),
            entity_key_type=SUPPORTED_ENTITY_KEY_TYPE,
            data_as_of=page.data_as_of,
            source_watermark=page.watermark,
            evidence_refs=dict(page.evidence_refs),
        )

    def _id_list_result(
        self,
        entity_type: str,
        first_page: SnapshotPage,
        limit: int | None,
    ) -> MCPResult:
        entity_ids: list[int] = list(first_page.entity_ids or ())
        scores: dict[int, float] = dict(first_page.scores)
        evidence_refs: dict[int, str] = dict(first_page.evidence_refs)
        data_as_of = first_page.data_as_of
        watermark = first_page.watermark
        cursor = first_page.next_cursor
        while cursor is not None:
            page = self._client.fetch_snapshot(cursor=cursor)
            entity_ids.extend(page.entity_ids or ())
            scores.update(page.scores)
            evidence_refs.update(page.evidence_refs)
            data_as_of = page.data_as_of
            watermark = page.watermark
            cursor = page.next_cursor

        if limit is not None and len(entity_ids) > limit:
            entity_ids = sorted(
                entity_ids, key=lambda i: (-scores.get(i, 1.0), i)
            )[:limit]
            kept = set(entity_ids)
            scores = {i: s for i, s in scores.items() if i in kept}
            evidence_refs = {
                i: r for i, r in evidence_refs.items() if i in kept
            }

        if len(entity_ids) >= self._bitmap_threshold:
            return self._encode_as_bitmap(
                entity_type, entity_ids, scores, evidence_refs,
                data_as_of, watermark,
            )
        return MCPResult(
            entity_type=entity_type,
            entity_ids=entity_ids,
            scores=scores,
            entity_key_type=SUPPORTED_ENTITY_KEY_TYPE,
            data_as_of=data_as_of,
            source_watermark=watermark,
            evidence_refs=evidence_refs,
        )

    @staticmethod
    def _encode_as_bitmap(
        entity_type: str,
        entity_ids: list[int],
        scores: dict[int, float],
        evidence_refs: dict[int, str],
        data_as_of: str,
        watermark: str,
    ) -> MCPResult:
        from pyroaring import BitMap64

        payload = BitMap64(entity_ids).serialize()
        return MCPResult(
            entity_type=entity_type,
            membership_bitmap=payload,
            bitmap_encoding=SUPPORTED_BITMAP_ENCODING,
            scores=scores,
            entity_key_type=SUPPORTED_ENTITY_KEY_TYPE,
            data_as_of=data_as_of,
            source_watermark=watermark,
            evidence_refs=evidence_refs,
        )
