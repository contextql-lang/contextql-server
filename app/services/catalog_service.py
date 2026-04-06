from __future__ import annotations

import json
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
        """Insert a new context in *draft* state (version 1)."""
        tags_json = json.dumps(tags) if tags is not None else None

        cursor = self.conn.execute(
            """
            INSERT INTO contexts
                (name, namespace, version, definition_text, entity_key,
                 has_score, score_column, description, tags, classification,
                 lifecycle_state)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 'draft')
            """,
            (
                name, namespace, definition_text, entity_key,
                int(has_score), score_column, description, tags_json,
                classification,
            ),
        )
        self.conn.commit()

        self._audit_log("context.created", name, namespace, {"version": 1})
        return self.get(name, namespace)  # type: ignore[return-value]

    def get(self, name: str, namespace: str = "default") -> dict | None:
        """Return the latest version of a context by name + namespace."""
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute(
            """
            SELECT * FROM contexts
            WHERE name = ? AND namespace = ?
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
        clauses: list[str] = []
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
        total = self.conn.execute(count_sql, params * 2).fetchone()[0]

        data_sql = f"{base} ORDER BY c.name LIMIT ? OFFSET ?"
        all_params = params * 2 + [limit, offset]
        rows = self.conn.execute(data_sql, all_params).fetchall()

        return [self._row_to_dict(r) for r in rows], total

    def update(self, name: str, namespace: str = "default", **kwargs) -> dict:
        """Create a new version by copying the latest and overriding fields."""
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Context '{name}' not found in namespace '{namespace}'")

        new_version = current["version"] + 1

        # Fields that may be overridden
        updatable = (
            "definition_text", "entity_key", "has_score", "score_column",
            "description", "tags", "classification", "dependency_refs",
            "provider_refs",
        )
        merged = {k: kwargs.get(k, current[k]) for k in updatable}

        tags_json = json.dumps(merged["tags"]) if merged["tags"] is not None else None
        dep_json = json.dumps(merged["dependency_refs"]) if merged["dependency_refs"] is not None else None
        prov_json = json.dumps(merged["provider_refs"]) if merged["provider_refs"] is not None else None

        self.conn.execute(
            """
            INSERT INTO contexts
                (name, namespace, version, definition_text, entity_key,
                 has_score, score_column, description, tags, classification,
                 lifecycle_state, dependency_refs, provider_refs)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                name, namespace, new_version,
                merged["definition_text"], merged["entity_key"],
                int(merged["has_score"]), merged["score_column"],
                merged["description"], tags_json, merged["classification"],
                dep_json, prov_json,
            ),
        )
        self.conn.commit()

        self._audit_log("context.updated", name, namespace, {"version": new_version})
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

        self.conn.execute(
            "DELETE FROM contexts WHERE name = ? AND namespace = ?",
            (name, namespace),
        )
        self.conn.commit()
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

        # Attempt validation via the engine's explain/parser if available
        if self.engine is not None:
            try:
                self.engine.explain(current["definition_text"])
            except Exception as exc:
                raise ValueError(f"Validation failed: {exc}") from exc

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """
            UPDATE contexts
            SET lifecycle_state = 'validated',
                last_validated_at = ?,
                updated_at = ?
            WHERE name = ? AND namespace = ? AND version = ?
            """,
            (now, now, name, namespace, current["version"]),
        )
        self.conn.commit()

        self._audit_log("context.validated", name, namespace, {"version": current["version"]})
        return self.get(name, namespace)  # type: ignore[return-value]

    def activate(self, name: str, namespace: str = "default") -> dict:
        """Transition to 'active' and register with the engine."""
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Context '{name}' not found in namespace '{namespace}'")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """
            UPDATE contexts
            SET lifecycle_state = 'active', updated_at = ?
            WHERE name = ? AND namespace = ? AND version = ?
            """,
            (now, name, namespace, current["version"]),
        )
        self.conn.commit()

        if self.engine is not None:
            self.engine.register_context(
                current["name"],
                current["definition_text"],
                entity_key=current["entity_key"],
                has_score=bool(current["has_score"]),
                score_column=current.get("score_column"),
            )

        self._audit_log("context.activated", name, namespace, {"version": current["version"]})
        return self.get(name, namespace)  # type: ignore[return-value]

    def retire(self, name: str, namespace: str = "default") -> dict:
        """Transition to 'retired'."""
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Context '{name}' not found in namespace '{namespace}'")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """
            UPDATE contexts
            SET lifecycle_state = 'retired', updated_at = ?
            WHERE name = ? AND namespace = ? AND version = ?
            """,
            (now, name, namespace, current["version"]),
        )
        self.conn.commit()

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

        result = self.engine.execute(current["definition_text"])
        rows = result.to_pandas().head(limit).to_dict(orient="records")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            """
            UPDATE contexts SET last_executed_at = ?
            WHERE name = ? AND namespace = ? AND version = ?
            """,
            (now, name, namespace, current["version"]),
        )
        self.conn.commit()

        return rows

    # ------------------------------------------------------------------
    # Engine sync
    # ------------------------------------------------------------------

    def sync_active_to_engine(self) -> None:
        """Load all active contexts and register them in the engine (startup)."""
        if self.engine is None:
            return

        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            """
            SELECT c.* FROM contexts c
            INNER JOIN (
                SELECT name, namespace, MAX(version) AS max_ver
                FROM contexts
                WHERE lifecycle_state = 'active'
                GROUP BY name, namespace
            ) latest
            ON c.name = latest.name
               AND c.namespace = latest.namespace
               AND c.version = latest.max_ver
            """,
        ).fetchall()

        for row in rows:
            ctx = self._row_to_dict(row)
            self.engine.register_context(
                ctx["name"],
                ctx["definition_text"],
                entity_key=ctx["entity_key"],
                has_score=bool(ctx["has_score"]),
                score_column=ctx.get("score_column"),
            )

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
