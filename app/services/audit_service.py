from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone


class AuditService:
    """Append-only audit log service backed by SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def log(
        self,
        event_type: str,
        resource_type: str | None = None,
        resource_name: str | None = None,
        detail: dict | None = None,
        actor: str = "system",
        namespace: str = "default",
        trace_id: str | None = None,
    ) -> int:
        """Insert an audit event and return the new row id."""
        if trace_id is None:
            trace_id = uuid.uuid4().hex
        detail_json = json.dumps(detail) if detail is not None else None
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        cursor = self._conn.execute(
            """
            INSERT INTO audit_log (timestamp, event_type, actor, namespace,
                                   resource_type, resource_name, detail, trace_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (timestamp, event_type, actor, namespace,
             resource_type, resource_name, detail_json, trace_id),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def query(
        self,
        event_type: str | None = None,
        namespace: str | None = None,
        resource_type: str | None = None,
        resource_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Return audit entries matching the given filters."""
        clauses: list[str] = []
        params: list[str | int] = []

        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if namespace is not None:
            clauses.append("namespace = ?")
            params.append(namespace)
        if resource_type is not None:
            clauses.append("resource_type = ?")
            params.append(resource_type)
        if resource_name is not None:
            clauses.append("resource_name = ?")
            params.append(resource_name)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM audit_log{where} ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(sql, params).fetchall()

        results: list[dict] = []
        for row in rows:
            entry = dict(row)
            if entry.get("detail") is not None:
                try:
                    entry["detail"] = json.loads(entry["detail"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(entry)
        return results
