from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone


class CatalogService:
    """Context catalog service backed by SQLite."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        engine=None,
        audit=None,
    ) -> None:
        self.conn = conn
        self.engine = engine    # contextql Engine, optional
        self.audit = audit      # AuditService, optional
        self.repository = None
        if self.engine is not None:
            from app.repositories import SQLiteContextRuntimeRepository
            from contextql.catalog_repository import InMemoryCatalogRepository

            if isinstance(
                getattr(self.engine, "_catalog_repository", None),
                InMemoryCatalogRepository,
            ):
                repository = SQLiteContextRuntimeRepository(conn)
                self.engine._catalog_repository = repository
                self.engine._executor.ddl.repository = repository
            self.repository = self.engine._catalog_repository

    def _qualified(self, name: str, namespace: str) -> str:
        identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        if not identifier.match(name) or not identifier.match(namespace):
            raise ValueError("Context name and namespace must be identifiers")
        return name if namespace == "default" else f"{namespace}.{name}"

    @staticmethod
    def _literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        definition_text: str,
        entity_key: str,
        namespace: str = "default",
        has_score: bool = False,
        score_column: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        classification: str = "internal",
    ) -> dict:
        """Create through executable DDL, the sole catalog write path."""
        if self.engine is None:
            raise RuntimeError("Engine is required for context creation")
        qualified = self._qualified(name, namespace)
        score_clause = ""
        if has_score:
            if not score_column:
                raise ValueError("score_column is required when has_score is true")
            score_clause = f"\nSCORE {score_column}"
        description_clause = (
            f"\nDESCRIPTION {self._literal(description)}"
            if description else ""
        )
        tags_clause = ""
        if tags:
            tags_clause = "\nTAGS (" + ", ".join(
                self._literal(tag) for tag in tags
            ) + ")"
        ddl = (
            f"CREATE CONTEXT {qualified} ON {entity_key}"
            f"{score_clause}{description_clause}{tags_clause}\n"
            f"AS {definition_text.rstrip(';')};"
        )
        self.engine.execute(ddl)
        self.engine.execute(
            f"ALTER CONTEXT {qualified} SET STATE 'draft';"
        )

        self._audit_log("context.created", name, namespace, {"version": 1})
        return self.get(name, namespace)  # type: ignore[return-value]

    def get(self, name: str, namespace: str = "default") -> dict | None:
        """Return the latest version of a context by name + namespace."""
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute(
            """
            SELECT * FROM contexts
            WHERE name = ? AND namespace = ? AND dropped_at IS NULL
            ORDER BY version DESC
            LIMIT 1
            """,
            (name, namespace),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(
        self,
        namespace: str | None = None,
        lifecycle_state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """List contexts with optional filters. Returns (rows, total_count)."""
        clauses: list[str] = ["dropped_at IS NULL"]
        params: list[str | int] = []

        if namespace is not None:
            clauses.append("namespace = ?")
            params.append(namespace)
        if lifecycle_state is not None:
            clauses.append("lifecycle_state = ?")
            params.append(lifecycle_state)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        # Only return latest version per (name, namespace)
        base = f"""
            SELECT c.* FROM contexts c
            INNER JOIN (
                SELECT name, namespace, MAX(version) AS max_ver
                FROM contexts{where}
                GROUP BY name, namespace
            ) latest
            ON c.name = latest.name
               AND c.namespace = latest.namespace
               AND c.version = latest.max_ver
        """

        count_sql = f"SELECT COUNT(*) FROM ({base})"
        self.conn.row_factory = sqlite3.Row
        total = self.conn.execute(count_sql, params).fetchone()[0]

        data_sql = f"{base} ORDER BY c.name LIMIT ? OFFSET ?"
        all_params = params + [limit, offset]
        rows = self.conn.execute(data_sql, all_params).fetchall()

        return [self._row_to_dict(r) for r in rows], total

    def update(self, name: str, namespace: str = "default", **kwargs) -> dict:
        """Apply updates through ContextQL ALTER statements."""
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Context '{name}' not found in namespace '{namespace}'")

        if self.engine is None:
            raise RuntimeError("Engine is required for context updates")
        qualified = self._qualified(name, namespace)
        if "definition_text" in kwargs:
            self.engine.execute(
                f"ALTER CONTEXT {qualified} SET DEFINITION AS "
                f"{kwargs['definition_text'].rstrip(';')};"
            )
        if "description" in kwargs:
            self.engine.execute(
                f"ALTER CONTEXT {qualified} SET DESCRIPTION "
                f"{self._literal(kwargs['description'] or '')};"
            )
        if "tags" in kwargs:
            values = ", ".join(
                self._literal(tag) for tag in (kwargs["tags"] or [])
            )
            self.engine.execute(
                f"ALTER CONTEXT {qualified} SET TAGS ({values});"
            )
        if "score_column" in kwargs:
            if kwargs["score_column"]:
                self.engine.execute(
                    f"ALTER CONTEXT {qualified} SET SCORE "
                    f"{kwargs['score_column']};"
                )
            else:
                self.engine.execute(
                    f"ALTER CONTEXT {qualified} DROP SCORE;"
                )
        self.engine.execute(
            f"ALTER CONTEXT {qualified} SET STATE 'draft';"
        )

        updated = self.get(name, namespace)
        self._audit_log(
            "context.updated",
            name,
            namespace,
            {"version": updated["version"] if updated else None},
        )
        return self.get(name, namespace)  # type: ignore[return-value]

    def delete(self, name: str, namespace: str = "default") -> bool:
        """Delete a context — only allowed when lifecycle_state is 'draft'."""
        current = self.get(name, namespace)
        if current is None:
            return False
        if current["lifecycle_state"] != "draft":
            raise ValueError(
                f"Cannot delete context in '{current['lifecycle_state']}' state; "
                "only 'draft' contexts may be deleted"
            )

        if self.engine is None:
            raise RuntimeError("Engine is required for context deletion")
        self.engine.execute(
            f"DROP CONTEXT {self._qualified(name, namespace)};"
        )
        self._audit_log("context.deleted", name, namespace)
        return True

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def validate(self, name: str, namespace: str = "default") -> dict:
        """Validate a context definition and transition to 'validated'."""
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Context '{name}' not found in namespace '{namespace}'")

        if self.engine is not None:
            qualified = self._qualified(name, namespace)
            self.engine.execute(f"VALIDATE CONTEXT {qualified};")
            self.engine.execute(
                f"ALTER CONTEXT {qualified} SET STATE 'validated';"
            )

        self._audit_log("context.validated", name, namespace, {"version": current["version"]})
        return self.get(name, namespace)  # type: ignore[return-value]

    def activate(self, name: str, namespace: str = "default") -> dict:
        """Transition to 'active' and register with the engine."""
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Context '{name}' not found in namespace '{namespace}'")

        if self.engine is not None:
            self.engine.execute(
                f"ALTER CONTEXT {self._qualified(name, namespace)} "
                "SET STATE 'active';"
            )

        self._audit_log("context.activated", name, namespace, {"version": current["version"]})
        return self.get(name, namespace)  # type: ignore[return-value]

    def retire(self, name: str, namespace: str = "default") -> dict:
        """Transition to 'retired'."""
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Context '{name}' not found in namespace '{namespace}'")

        if self.engine is not None:
            self.engine.execute(
                f"ALTER CONTEXT {self._qualified(name, namespace)} "
                "SET STATE 'retired';"
            )

        self._audit_log("context.retired", name, namespace, {"version": current["version"]})
        return self.get(name, namespace)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Versions & preview
    # ------------------------------------------------------------------

    def versions(self, name: str, namespace: str = "default") -> list[dict]:
        """Return all versions ordered by version number."""
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            """
            SELECT * FROM contexts
            WHERE name = ? AND namespace = ?
            ORDER BY version ASC
            """,
            (name, namespace),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def preview(self, name: str, namespace: str = "default", limit: int = 10) -> list[dict]:
        """Execute the context definition SQL and return sample rows."""
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Context '{name}' not found in namespace '{namespace}'")
        if self.engine is None:
            raise RuntimeError("Engine is not available for preview")

        definition_sql = current.get("definition_sql")
        if not definition_sql:
            raise ValueError("Context has no executable SQL definition")
        result = self.engine.execute(definition_sql)
        rows = result.to_pandas().head(limit).to_dict(orient="records")

        if self.repository is not None and hasattr(
            self.repository, "record_execution"
        ):
            self.repository.record_execution(
                current["context_id"],
                current["version"],
                datetime.now(timezone.utc),
            )

        return rows

    def refresh(self, name: str, namespace: str = "default") -> dict:
        if self.engine is None:
            raise RuntimeError("Engine is required for refresh")
        self.engine.execute(
            f"REFRESH CONTEXT {self._qualified(name, namespace)};"
        )
        return self.get(name, namespace)  # type: ignore[return-value]

    def snapshots(
        self,
        name: str,
        namespace: str = "default",
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Context '{name}' not found")
        rows = self.conn.execute(
            """
            SELECT context_id, version, storage_kind, member_count,
                   serialized_bytes, computed_at, data_as_of, valid_from,
                   valid_to, definition_hash, source_watermark, state,
                   error_detail
            FROM context_snapshots
            WHERE context_id = ?
            ORDER BY version DESC
            LIMIT ? OFFSET ?
            """,
            (current["context_id"], limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    def history(
        self,
        name: str,
        namespace: str = "default",
        *,
        start: str | None = None,
        end: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Context '{name}' not found")
        clauses = ["context_id = ?"]
        params: list = [current["context_id"]]
        if start is not None:
            clauses.append("effective_at >= ?")
            params.append(start)
        if end is not None:
            clauses.append("effective_at <= ?")
            params.append(end)
        params.extend([limit, offset])
        rows = self.conn.execute(
            f"""
            SELECT id, transaction_id, change_type, recorded_at,
                   effective_at, context_version, source, evidence_ref,
                   previous_score, new_score
            FROM context_membership_history
            WHERE {' AND '.join(clauses)}
            ORDER BY effective_at, id
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Engine sync
    # ------------------------------------------------------------------

    def sync_active_to_engine(self) -> None:
        """Compatibility no-op: Engine hydration now owns startup loading."""
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a Row to a dict, parsing JSON fields."""
        d = dict(row)
        # Boolean coercion
        d["has_score"] = bool(d.get("has_score"))
        # JSON list/object fields
        for field in ("tags", "dependency_refs", "provider_refs"):
            raw = d.get(field)
            if raw is not None:
                try:
                    d[field] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass
        # JSON object fields
        if d.get("freshness_metadata") is not None:
            try:
                d["freshness_metadata"] = json.loads(d["freshness_metadata"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    def _audit_log(
        self,
        event_type: str,
        resource_name: str,
        namespace: str,
        detail: dict | None = None,
    ) -> None:
        if self.audit is not None:
            self.audit.log(
                event_type=event_type,
                resource_type="context",
                resource_name=resource_name,
                namespace=namespace,
                detail=detail,
            )
