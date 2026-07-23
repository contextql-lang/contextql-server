"""DeepSee REMOTE provider: relational settlement evidence.

Implements the engine's ``RemoteProvider`` protocol for the two supported
resources (``settlement_cases``, ``reconciliation_evidence``). Evidence rows
stay separate from lightweight membership (plan 8.1) and every result
carries ``schema``, ``data_as_of``, and ``source_watermark``.
"""
from __future__ import annotations

from contextql.providers import EntityFilter, RemoteResult

from app.connectors.deepsee.client import DeepSeeClient
from app.connectors.deepsee.mock_service import VALID_RESOURCES
from app.connectors.deepsee.models import contract_error


class DeepSeeRemoteProvider:
    """REMOTE provider backed by a :class:`DeepSeeClient`."""

    #: Resources this provider serves (matches the planned registration).
    RESOURCES = frozenset(VALID_RESOURCES)

    def __init__(self, client: DeepSeeClient) -> None:
        self._client = client

    def query(
        self,
        resource: str,
        filters: dict,
        columns: list[str],
        limit: int | None = None,
        *,
        entity_filter: EntityFilter | None = None,
    ) -> RemoteResult:
        """Fetch evidence rows for one of the supported resources."""
        if resource not in self.RESOURCES:
            raise contract_error(
                f"Unknown DeepSee resource {resource!r}; expected one of "
                f"{sorted(self.RESOURCES)}."
            )
        bounded_filters = dict(filters) if filters else {}
        requested_ids: set[int] | None = None
        if entity_filter is not None:
            requested_ids = {int(value) for value in entity_filter.ids()}
            bounded_filters[entity_filter.column] = tuple(requested_ids)
        response = self._client.fetch_cases(
            resource,
            filters=bounded_filters or None,
            columns=list(columns) if columns else None,
            limit=limit,
        )
        rows = [dict(row) for row in response.rows]
        if requested_ids is not None:
            outside = {
                int(row[entity_filter.column])
                for row in rows
                if int(row[entity_filter.column]) not in requested_ids
            }
            if outside:
                raise contract_error(
                    "DeepSee returned evidence outside the requested "
                    "entity filter."
                )
        return RemoteResult(
            rows=rows,
            schema=dict(response.schema),
            data_as_of=response.data_as_of,
            source_watermark=response.watermark,
            next_cursor=response.next_cursor,
        )
