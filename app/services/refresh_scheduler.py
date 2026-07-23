"""Background scheduler for contexts using refresh_mode='scheduled'."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from contextql.context_options import parse_duration_seconds

logger = logging.getLogger(__name__)


class RefreshScheduler:
    def __init__(
        self,
        engine,
        *,
        audit=None,
        poll_seconds: float = 5.0,
        monotonic=time.monotonic,
        now=lambda: datetime.now(timezone.utc),
    ) -> None:
        self.engine = engine
        self.audit = audit
        self.poll_seconds = max(float(poll_seconds), 0.05)
        self._monotonic = monotonic
        self._now = now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._locks: dict[str, threading.Lock] = {}
        self._next_due: dict[str, float] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="contextql-refresh-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                raise RuntimeError(
                    "Refresh scheduler did not stop before timeout."
                )

    def tick(self) -> None:
        current = self._monotonic()
        for entry in list(self.engine._catalog.list_contexts()):
            settings = entry.materialization
            if (
                not settings.materialized
                or settings.refresh_mode != "scheduled"
            ):
                continue
            interval = parse_duration_seconds(settings.refresh_interval)
            if interval is None:
                continue
            context_id = entry.context_id
            due = self._next_due.get(context_id, current)
            if current < due:
                continue
            lock = self._locks.setdefault(context_id, threading.Lock())
            if not lock.acquire(blocking=False):
                continue
            try:
                self._refresh(entry)
            finally:
                lock.release()
                jitter = interval * (
                    (int(context_id.replace("-", "")[:4], 16) % 1000)
                    / 10_000
                )
                self._next_due[context_id] = current + interval + jitter

    def _refresh(self, entry) -> None:
        try:
            self.engine.execute(
                f"REFRESH CONTEXT {entry.qualified_name};"
            )
            if self.audit is not None:
                self.audit.log(
                    "context.refresh.succeeded",
                    resource_type="context",
                    resource_name=entry.qualified_name,
                    namespace=entry.namespace,
                    detail={"context_id": entry.context_id},
                )
        except Exception as exc:
            self.engine._executor.ddl.record_refresh_failure(
                entry.qualified_name, str(exc)
            )
            if self.audit is not None:
                self.audit.log(
                    "context.refresh.failed",
                    resource_type="context",
                    resource_name=entry.qualified_name,
                    namespace=entry.namespace,
                    detail={
                        "context_id": entry.context_id,
                        "error": str(exc),
                    },
                )
            logger.exception(
                "Scheduled refresh failed for %s", entry.qualified_name
            )

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            self.tick()
