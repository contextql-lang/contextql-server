from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


class IdentityService:
    """Identity map registry service backed by SQLite."""

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

    def register(
        self,
        name: str,
        source_system: str,
        source_entity_path: str,
        target_system: str,
        target_entity_path: str,
        namespace: str = "default",
        matching_mode: str = "exact",
        confidence: float = 1.0,
        description: str | None = None,
    ) -> dict:
        """Register a new identity map."""
        self.conn.execute(
            """
            INSERT INTO identity_maps
                (name, namespace, source_system, source_entity_path,
                 target_system, target_entity_path, matching_mode,
                 confidence, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name, namespace, source_system, source_entity_path,
                target_system, target_entity_path, matching_mode,
                confidence, description,
            ),
        )
        self.conn.commit()

        self._audit_log("identity_map.registered", name, namespace)
        return self.get(name, namespace)  # type: ignore[return-value]

    def get(self, name: str, namespace: str = "default") -> dict | None:
        """Return an identity map by name + namespace."""
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute(
            "SELECT * FROM identity_maps WHERE name = ? AND namespace = ?",
            (name, namespace),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(
        self,
        namespace: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """List identity maps with optional namespace filter."""
        clauses: list[str] = []
        params: list[str | int] = []

        if namespace is not None:
            clauses.append("namespace = ?")
            params.append(namespace)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        total = self.conn.execute(
            f"SELECT COUNT(*) FROM identity_maps{where}", params,
        ).fetchone()[0]

        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            f"SELECT * FROM identity_maps{where} ORDER BY name LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        return [self._row_to_dict(r) for r in rows], total

    # ------------------------------------------------------------------
    # Engine sync
    # ------------------------------------------------------------------

    def sync_to_engine(self) -> None:
        """Register all identity maps in the engine (startup)."""
        if self.engine is None:
            return

        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute("SELECT * FROM identity_maps").fetchall()

        for row in rows:
            im = self._row_to_dict(row)
            self.engine.register_identity_map(
                im["name"],
                {im["source_entity_path"]: im["target_entity_path"]},
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a Row to a dict."""
        return dict(row)

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
                resource_type="identity_map",
                resource_name=resource_name,
                namespace=namespace,
                detail=detail,
            )
