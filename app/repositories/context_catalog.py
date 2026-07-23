"""SQLite implementation of the ContextQL catalog/runtime repository."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterable

from contextql.history import MembershipChange
from contextql.semantic import (
    ContextCatalogEntry,
    ContextComposition,
    ContextCompositionItem,
    EntityKeyType,
    MaterializationSettings,
)
from contextql.snapshot_codec import decode_scores, encode_scores


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class SQLiteContextRuntimeRepository:
    """One durable write path for definitions and snapshot promotion."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.RLock()
        self._backfill_v2_rows()

    def _backfill_v2_rows(self) -> None:
        rows = self.conn.execute(
            """
            SELECT name, namespace, definition_text
            FROM contexts
            WHERE context_id IS NULL
            GROUP BY name, namespace
            """
        ).fetchall()
        for row in rows:
            context_id = str(uuid.uuid4())
            definition = row["definition_text"] or ""
            definition_hash = hashlib.sha256(
                definition.encode("utf-8")
            ).hexdigest()
            self.conn.execute(
                """
                UPDATE contexts
                SET context_id = ?,
                    definition_hash = COALESCE(definition_hash, ?),
                    namespace = COALESCE(namespace, 'default')
                WHERE name = ? AND namespace = ?
                """,
                (
                    context_id,
                    definition_hash,
                    row["name"],
                    row["namespace"],
                ),
            )
        self.conn.commit()

    def load_contexts(self) -> Iterable[ContextCatalogEntry]:
        rows = self.conn.execute(
            """
            SELECT c.*
            FROM contexts c
            JOIN (
                SELECT context_id, MAX(version) AS max_version
                FROM contexts
                WHERE context_id IS NOT NULL AND dropped_at IS NULL
                GROUP BY context_id
            ) latest
              ON latest.context_id = c.context_id
             AND latest.max_version = c.version
            WHERE c.dropped_at IS NULL
            ORDER BY c.namespace, c.name
            """
        ).fetchall()
        return [self._entry_from_row(row) for row in rows]

    def save_context(
        self,
        entry: ContextCatalogEntry,
        *,
        raw_ddl: str | None = None,
    ) -> None:
        materialization = json.dumps(asdict(entry.materialization))
        composition = None
        if entry.composition is not None:
            composition = json.dumps(
                {
                    "strategy": entry.composition.strategy,
                    "items": [
                        {"name": item.name, "weight": item.weight}
                        for item in entry.composition.items
                    ],
                }
            )
        with self._lock:
            existing = self.conn.execute(
                """
                SELECT id FROM contexts
                WHERE context_id = ? AND version = ?
                """,
                (entry.context_id, entry.version),
            ).fetchone()
            values = {
                "name": entry.name,
                "namespace": entry.namespace,
                "version": entry.version,
                # Keep the REST-era field as the executable definition body.
                # Raw ContextQL DDL is stored independently in ``raw_ddl``.
                "definition_text": entry.definition_sql or "",
                "entity_key": entry.entity_key_name,
                "entity_key_type": entry.entity_key_type.value,
                "has_score": int(entry.has_score),
                "score_column": entry.score_expression,
                "description": entry.description,
                "tags": json.dumps(entry.tags),
                "lifecycle_state": entry.lifecycle_state,
                "dependency_refs": json.dumps(entry.dependencies),
                "context_id": entry.context_id,
                "definition_hash": entry.definition_hash,
                "raw_ddl": raw_ddl,
                "materialization_json": materialization,
                "current_snapshot_version": entry.current_snapshot_version,
                "last_refreshed_at": _iso(entry.last_refreshed_at),
                "data_as_of": _iso(entry.data_as_of),
                "last_refresh_error": entry.last_refresh_error,
                "history_available_from": _iso(
                    entry.history_available_from
                ),
                "definition_sql": entry.definition_sql,
                "score_expression": entry.score_expression,
                "composition_json": composition,
                "temporal_column": entry.temporal_column,
                "temporal_granularity": entry.temporal_granularity,
                "source_kind": entry.source_kind,
            }
            if existing is None:
                self.conn.execute(
                    """
                    INSERT INTO contexts (
                        name, namespace, version, definition_text, entity_key,
                        entity_key_type, has_score, score_column, description,
                        tags, lifecycle_state, dependency_refs, context_id,
                        definition_hash, raw_ddl, materialization_json,
                        current_snapshot_version, last_refreshed_at, data_as_of,
                        last_refresh_error, history_available_from,
                        definition_sql, score_expression, composition_json,
                        temporal_column, temporal_granularity, source_kind
                    ) VALUES (
                        :name, :namespace, :version, :definition_text,
                        :entity_key, :entity_key_type, :has_score,
                        :score_column, :description, :tags, :lifecycle_state,
                        :dependency_refs, :context_id, :definition_hash,
                        :raw_ddl, :materialization_json,
                        :current_snapshot_version, :last_refreshed_at,
                        :data_as_of, :last_refresh_error,
                        :history_available_from, :definition_sql,
                        :score_expression, :composition_json,
                        :temporal_column, :temporal_granularity, :source_kind
                    )
                    """,
                    values,
                )
            else:
                assignments = ", ".join(
                    f"{name} = :{name}" for name in values
                    if name not in {"context_id", "version"}
                    and not (name == "raw_ddl" and raw_ddl is None)
                )
                self.conn.execute(
                    f"""
                    UPDATE contexts SET {assignments},
                        updated_at = datetime('now')
                    WHERE context_id = :context_id AND version = :version
                    """,
                    values,
                )
            self.conn.commit()

    def drop_context(self, entry: ContextCatalogEntry) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE contexts
                SET dropped_at = ?, updated_at = datetime('now')
                WHERE context_id = ?
                """,
                (_iso(datetime.now(timezone.utc)), entry.context_id),
            )
            self.conn.commit()

    def record_execution(
        self, context_id: str, version: int, executed_at: datetime
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE contexts
                SET last_executed_at = ?, updated_at = datetime('now')
                WHERE context_id = ? AND version = ?
                """,
                (_iso(executed_at), context_id, version),
            )
            self.conn.commit()

    def promote_snapshot(
        self,
        entry: ContextCatalogEntry,
        snapshot,
        *,
        membership_payload: bytes,
        scores: dict[int, float],
        history_events: Iterable[MembershipChange] = (),
    ) -> None:
        score_payload = encode_scores(scores)
        membership_hash = hashlib.sha256(membership_payload).hexdigest()
        score_hash = hashlib.sha256(score_payload).hexdigest()
        events = list(history_events)
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.execute(
                    """
                    INSERT INTO context_snapshot_payloads (
                        context_id, version, membership_blob, score_blob
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        entry.context_id,
                        snapshot.version,
                        membership_payload,
                        score_payload,
                    ),
                )
                self.conn.execute(
                    """
                    INSERT INTO context_snapshots (
                        context_id, version, storage_kind, member_count,
                        serialized_bytes, computed_at, data_as_of, valid_from,
                        definition_hash, definition_version, source_watermark,
                        membership_sha256, score_sha256, state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'building')
                    """,
                    (
                        entry.context_id,
                        snapshot.version,
                        snapshot.storage_kind,
                        snapshot.member_count,
                        len(membership_payload),
                        _iso(snapshot.computed_at),
                        _iso(snapshot.data_as_of),
                        _iso(snapshot.valid_from),
                        snapshot.definition_hash,
                        entry.version,
                        snapshot.source_watermark,
                        membership_hash,
                        score_hash,
                    ),
                )
                self.conn.execute(
                    """
                    UPDATE context_snapshots
                    SET state = 'superseded', valid_to = ?
                    WHERE context_id = ? AND state = 'current'
                    """,
                    (_iso(snapshot.computed_at), entry.context_id),
                )
                self.conn.execute(
                    """
                    UPDATE context_snapshots
                    SET state = 'current'
                    WHERE context_id = ? AND version = ?
                    """,
                    (entry.context_id, snapshot.version),
                )
                self.conn.execute(
                    """
                    UPDATE contexts
                    SET current_snapshot_version = ?, last_refreshed_at = ?,
                        data_as_of = ?, last_refresh_error = NULL,
                        updated_at = datetime('now')
                    WHERE context_id = ? AND version = ?
                    """,
                    (
                        snapshot.version,
                        _iso(entry.last_refreshed_at),
                        _iso(entry.data_as_of),
                        entry.context_id,
                        entry.version,
                    ),
                )
                for event in events:
                    event_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            (
                                f"{entry.context_id}:{snapshot.version}:"
                                f"{event.entity_id}:{event.change_type}"
                            ),
                        )
                    )
                    self.conn.execute(
                        """
                        INSERT OR IGNORE INTO context_membership_history (
                            context_id, transaction_id, change_type,
                            recorded_at, effective_at, context_version,
                            definition_version, definition_hash, event_id,
                            source, evidence_ref, previous_score, new_score
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry.context_id,
                            event.entity_id,
                            event.change_type,
                            _iso(event.recorded_at),
                            _iso(event.effective_at),
                            event.context_version,
                            entry.version,
                            entry.definition_hash,
                            event_id,
                            event.source,
                            event.evidence_ref,
                            event.previous_score,
                            event.new_score,
                        ),
                    )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def hydrate_runtime(self, membership, history) -> None:
        rows = self.conn.execute(
            """
            SELECT s.*, p.membership_blob, p.score_blob
            FROM context_snapshots s
            JOIN context_snapshot_payloads p
              ON p.context_id = s.context_id AND p.version = s.version
            WHERE s.state IN ('current', 'superseded')
            ORDER BY s.context_id, s.version
            """
        ).fetchall()
        for row in rows:
            membership_payload = bytes(row["membership_blob"])
            score_payload = (
                bytes(row["score_blob"])
                if row["score_blob"] is not None else None
            )
            if hashlib.sha256(membership_payload).hexdigest() != (
                row["membership_sha256"]
            ):
                raise ValueError(
                    f"[E201] corrupt membership payload for "
                    f"{row['context_id']}@{row['version']}."
                )
            if score_payload is not None and (
                hashlib.sha256(score_payload).hexdigest()
                != row["score_sha256"]
            ):
                raise ValueError(
                    f"[E201] corrupt score payload for "
                    f"{row['context_id']}@{row['version']}."
                )
            restored = membership.deserialize(
                context_id=row["context_id"],
                payload=membership_payload,
                computed_at=_datetime(row["computed_at"]),
                data_as_of=_datetime(row["data_as_of"]),
                definition_hash=row["definition_hash"],
                source_watermark=row["source_watermark"],
                scores=decode_scores(score_payload),
                storage_kind=row["storage_kind"],
            )
            if restored.version != row["version"]:
                raise ValueError(
                    f"[E201] non-contiguous snapshot versions for "
                    f"{row['context_id']}."
                )

        event_rows = self.conn.execute(
            """
            SELECT * FROM context_membership_history
            ORDER BY id
            """
        ).fetchall()
        history.append(
            MembershipChange(
                context_id=row["context_id"],
                entity_id=row["transaction_id"],
                change_type=row["change_type"],
                recorded_at=_datetime(row["recorded_at"]),
                effective_at=_datetime(row["effective_at"]),
                context_version=row["context_version"],
                source=row["source"],
                evidence_ref=row["evidence_ref"],
                previous_score=row["previous_score"],
                new_score=row["new_score"],
            )
            for row in event_rows
        )

    def prune_history(self, entry, cutoff) -> None:
        """Delete state older than the retained anchor snapshot."""
        with self._lock:
            anchor = self.conn.execute(
                """
                SELECT version FROM context_snapshots
                WHERE context_id = ? AND data_as_of <= ?
                ORDER BY data_as_of DESC, version DESC
                LIMIT 1
                """,
                (entry.context_id, _iso(cutoff)),
            ).fetchone()
            if anchor is None:
                return
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.execute(
                    """
                    DELETE FROM context_snapshot_payloads
                    WHERE context_id = ? AND version < ?
                    """,
                    (entry.context_id, anchor["version"]),
                )
                self.conn.execute(
                    """
                    DELETE FROM context_snapshots
                    WHERE context_id = ? AND version < ?
                    """,
                    (entry.context_id, anchor["version"]),
                )
                self.conn.execute(
                    """
                    DELETE FROM context_membership_history
                    WHERE context_id = ? AND effective_at < ?
                    """,
                    (entry.context_id, _iso(cutoff)),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def _entry_from_row(self, row: sqlite3.Row) -> ContextCatalogEntry:
        materialization_data = json.loads(
            row["materialization_json"] or "{}"
        )
        composition = None
        if row["composition_json"]:
            raw = json.loads(row["composition_json"])
            composition = ContextComposition(
                strategy=raw["strategy"],
                items=tuple(
                    ContextCompositionItem(
                        name=item["name"], weight=item.get("weight")
                    )
                    for item in raw["items"]
                ),
            )
        try:
            key_type = EntityKeyType(
                row["entity_key_type"] or "UNKNOWN"
            )
        except ValueError:
            key_type = EntityKeyType.UNKNOWN
        return ContextCatalogEntry(
            name=row["name"],
            namespace=row["namespace"] or "default",
            context_id=row["context_id"],
            version=row["version"],
            definition_hash=row["definition_hash"],
            entity_key_name=row["entity_key"],
            entity_key_type=key_type,
            has_score=bool(row["has_score"]),
            is_temporal=bool(row["temporal_column"]),
            lifecycle_state=row["lifecycle_state"],
            materialization=MaterializationSettings(
                **materialization_data
            ),
            current_snapshot_version=row["current_snapshot_version"],
            last_refreshed_at=_datetime(row["last_refreshed_at"]),
            data_as_of=_datetime(row["data_as_of"]),
            description=row["description"],
            tags=json.loads(row["tags"] or "[]"),
            dependencies=json.loads(row["dependency_refs"] or "[]"),
            definition_sql=row["definition_sql"],
            composition=composition,
            score_expression=row["score_expression"],
            temporal_column=row["temporal_column"],
            temporal_granularity=row["temporal_granularity"],
            source_kind=row["source_kind"] or "native",
            history_available_from=_datetime(
                row["history_available_from"]
            ),
            last_refresh_error=row["last_refresh_error"],
        )
