from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


class ProviderService:
    """Provider registry service backed by SQLite."""

    def __init__(self, conn: sqlite3.Connection, audit=None) -> None:
        self.conn = conn
        self.audit = audit  # AuditService, optional

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        provider_type: str,
        namespace: str = "default",
        endpoint: str | None = None,
        timeout_ms: int = 30000,
        entity_key_type: str | None = None,
        trust_tier: str = "standard",
    ) -> dict:
        """Register a new provider."""
        self.conn.execute(
            """
            INSERT INTO providers
                (name, namespace, provider_type, endpoint, timeout_ms,
                 entity_key_type, trust_tier)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, namespace, provider_type, endpoint, timeout_ms,
             entity_key_type, trust_tier),
        )
        self.conn.commit()

        self._audit_log("provider.registered", name, namespace)
        return self.get(name, namespace)  # type: ignore[return-value]

    def get(self, name: str, namespace: str = "default") -> dict | None:
        """Return a provider by name + namespace."""
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute(
            "SELECT * FROM providers WHERE name = ? AND namespace = ?",
            (name, namespace),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(
        self,
        namespace: str | None = None,
        provider_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """List providers with optional filters. Returns (rows, total_count)."""
        clauses: list[str] = []
        params: list[str | int] = []

        if namespace is not None:
            clauses.append("namespace = ?")
            params.append(namespace)
        if provider_type is not None:
            clauses.append("provider_type = ?")
            params.append(provider_type)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        total = self.conn.execute(
            f"SELECT COUNT(*) FROM providers{where}", params,
        ).fetchone()[0]

        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            f"SELECT * FROM providers{where} ORDER BY name LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        return [self._row_to_dict(r) for r in rows], total

    def update(self, name: str, namespace: str = "default", **kwargs) -> dict:
        """Update mutable provider fields."""
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Provider '{name}' not found in namespace '{namespace}'")

        updatable = (
            "endpoint", "timeout_ms", "entity_key_type", "trust_tier",
            "credentials_ref", "resource_shape",
        )
        sets: list[str] = []
        params: list = []
        for field in updatable:
            if field in kwargs:
                sets.append(f"{field} = ?")
                value = kwargs[field]
                if isinstance(value, (dict, list)):
                    value = json.dumps(value)
                params.append(value)

        if not sets:
            return current

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        sets.append("updated_at = ?")
        params.append(now)

        params.extend([name, namespace])
        self.conn.execute(
            f"UPDATE providers SET {', '.join(sets)} WHERE name = ? AND namespace = ?",
            params,
        )
        self.conn.commit()

        self._audit_log("provider.updated", name, namespace)
        return self.get(name, namespace)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Enable / Disable
    # ------------------------------------------------------------------

    def enable(self, name: str, namespace: str = "default") -> dict:
        """Enable a provider."""
        return self._set_enabled(name, namespace, enabled=True)

    def disable(self, name: str, namespace: str = "default") -> dict:
        """Disable a provider."""
        return self._set_enabled(name, namespace, enabled=False)

    # ------------------------------------------------------------------
    # Health tracking
    # ------------------------------------------------------------------

    def record_health(
        self, name: str, namespace: str = "default", success: bool = True,
    ) -> dict:
        """Record a health check result for a provider."""
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Provider '{name}' not found in namespace '{namespace}'")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if success:
            self.conn.execute(
                """
                UPDATE providers
                SET health_state = 'healthy', last_success_at = ?, updated_at = ?
                WHERE name = ? AND namespace = ?
                """,
                (now, now, name, namespace),
            )
        else:
            self.conn.execute(
                """
                UPDATE providers
                SET health_state = 'unhealthy', last_failure_at = ?, updated_at = ?
                WHERE name = ? AND namespace = ?
                """,
                (now, now, name, namespace),
            )
        self.conn.commit()

        self._audit_log(
            "provider.health_recorded", name, namespace,
            {"success": success, "health_state": "healthy" if success else "unhealthy"},
        )
        return self.get(name, namespace)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_enabled(self, name: str, namespace: str, *, enabled: bool) -> dict:
        current = self.get(name, namespace)
        if current is None:
            raise ValueError(f"Provider '{name}' not found in namespace '{namespace}'")

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "UPDATE providers SET enabled = ?, updated_at = ? WHERE name = ? AND namespace = ?",
            (int(enabled), now, name, namespace),
        )
        self.conn.commit()

        event = "provider.enabled" if enabled else "provider.disabled"
        self._audit_log(event, name, namespace)
        return self.get(name, namespace)  # type: ignore[return-value]

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a Row to a dict, coercing types."""
        d = dict(row)
        d["enabled"] = bool(d.get("enabled"))
        # Parse JSON object fields
        if d.get("resource_shape") is not None:
            try:
                d["resource_shape"] = json.loads(d["resource_shape"])
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
                resource_type="provider",
                resource_name=resource_name,
                namespace=namespace,
                detail=detail,
            )
