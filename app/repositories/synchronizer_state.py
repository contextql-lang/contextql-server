"""Durable connector synchronizer state."""
from __future__ import annotations

import sqlite3
import threading


class SQLiteSynchronizerStateRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._lock = threading.RLock()

    def load(self, context_id: str) -> tuple[str | None, set[str]]:
        state = self.conn.execute(
            """
            SELECT committed_watermark FROM context_sync_state
            WHERE context_id = ?
            """,
            (context_id,),
        ).fetchone()
        events = self.conn.execute(
            """
            SELECT event_id FROM context_sync_events
            WHERE context_id = ?
            """,
            (context_id,),
        ).fetchall()
        return (
            state[0] if state is not None else None,
            {row[0] for row in events},
        )

    def commit(
        self,
        context_id: str,
        watermark: str,
        event_watermarks: dict[str, str],
    ) -> None:
        with self._lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                for event_id, event_watermark in event_watermarks.items():
                    self.conn.execute(
                        """
                        INSERT OR IGNORE INTO context_sync_events
                            (context_id, event_id, watermark)
                        VALUES (?, ?, ?)
                        """,
                        (context_id, event_id, event_watermark),
                    )
                self.conn.execute(
                    """
                    INSERT INTO context_sync_state (
                        context_id, committed_watermark, ordering_boundary
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(context_id) DO UPDATE SET
                        committed_watermark = excluded.committed_watermark,
                        ordering_boundary = excluded.ordering_boundary,
                        updated_at = datetime('now')
                    """,
                    (context_id, watermark, watermark),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
