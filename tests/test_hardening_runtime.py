"""Adversarial runtime coverage for hardening WP2, WP4, and WP5."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

import contextql as cql
from contextql.membership import SetMembershipStore

from app.connectors.deepsee.client import DeepSeeClient
from app.connectors.deepsee.mock_service import MockDeepSeeService
from app.connectors.deepsee.synchronizer import DeepSeeSynchronizer
from app.repositories import SQLiteContextRuntimeRepository
from app.repositories.synchronizer_state import (
    SQLiteSynchronizerStateRepository,
)
from app.services.refresh_scheduler import RefreshScheduler


def _migrations() -> Path:
    return Path(__file__).parents[1] / "app" / "db" / "migrations"


def _migrated_connection(path: Path | str = ":memory:"):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    for migration in sorted(_migrations().glob("*.sql")):
        conn.executescript(migration.read_text())
    conn.commit()
    return conn


def _scheduled_engine():
    engine = cql.Engine()
    engine.register_table(
        "txns",
        pd.DataFrame(
            {"txn_id": [1, 2], "status": ["failed", "open"]}
        ),
        primary_key="txn_id",
        primary_key_type="INT64",
    )
    engine.execute(
        """
        CREATE CONTEXT scheduled_failed ON txn_id
        WITH (
            materialized = TRUE,
            storage = 'set',
            refresh_mode = 'scheduled',
            refresh_interval = '10 seconds'
        )
        AS SELECT txn_id FROM txns WHERE status = 'failed';
        """
    )
    return engine


def test_scheduler_refreshes_only_when_due_and_records_failure():
    clock = [0.0]
    engine = _scheduled_engine()
    scheduler = RefreshScheduler(
        engine, monotonic=lambda: clock[0], poll_seconds=0.05
    )

    scheduler.tick()
    first = engine.membership.get_snapshot("scheduled_failed")
    assert first.version == 1

    clock[0] = 5.0
    scheduler.tick()
    assert engine.membership.get_snapshot(
        "scheduled_failed"
    ).version == 1

    engine._adapter.register_table(
        "txns", pd.DataFrame({"status": ["failed"]})
    )
    clock[0] = 12.0
    scheduler.tick()
    entry = engine._catalog.get_context("scheduled_failed")
    assert entry.last_refresh_error
    assert engine.membership.get_snapshot(
        "scheduled_failed"
    ).version == 1
    engine.register_table(
        "txns",
        pd.DataFrame(
            {"txn_id": [1, 2], "status": ["failed", "open"]}
        ),
        primary_key="txn_id",
        primary_key_type="INT64",
    )
    stale = engine.execute(
        "SELECT txn_id FROM txns "
        "WHERE CONTEXT IN (scheduled_failed);"
    )
    assert "W101" in {diagnostic.code for diagnostic in stale.diagnostics}

    clock[0] = 24.0
    scheduler.tick()
    assert engine._catalog.get_context(
        "scheduled_failed"
    ).last_refresh_error is None


def test_failed_sqlite_promotion_keeps_old_pointer_and_members(
    tmp_path, monkeypatch
):
    conn = _migrated_connection(tmp_path / "catalog.db")
    repository = SQLiteContextRuntimeRepository(conn)
    engine = cql.Engine(catalog_repository=repository)
    original = pd.DataFrame(
        {"txn_id": [1, 2], "status": ["failed", "open"]}
    )
    engine.register_table(
        "txns", original, primary_key="txn_id", primary_key_type="INT64"
    )
    engine.execute(
        """
        CREATE CONTEXT durable ON txn_id
        WITH (materialized = TRUE, storage = 'set')
        AS SELECT txn_id FROM txns WHERE status = 'failed';
        """
    )
    engine.execute("REFRESH CONTEXT durable;")
    entry = engine._catalog.get_context("durable")
    old_version = entry.current_snapshot_version

    engine.register_table(
        "txns",
        pd.DataFrame(
            {"txn_id": [1, 2], "status": ["open", "failed"]}
        ),
        primary_key="txn_id",
        primary_key_type="INT64",
    )
    monkeypatch.setattr(
        repository,
        "promote_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("forced promotion failure")
        ),
    )
    with pytest.raises(RuntimeError, match="forced promotion failure"):
        engine.execute("REFRESH CONTEXT durable;")

    assert engine.membership.get_snapshot("durable").version == old_version
    assert engine.membership.members("durable") == {1}
    row = conn.execute(
        """
        SELECT current_snapshot_version FROM contexts
        WHERE context_id = ? AND version = ?
        """,
        (entry.context_id, entry.version),
    ).fetchone()
    assert row[0] == old_version


def test_reader_sees_old_then_new_around_atomic_promotion(
    tmp_path, monkeypatch
):
    conn = _migrated_connection(tmp_path / "atomic-catalog.db")
    repository = SQLiteContextRuntimeRepository(conn)
    engine = cql.Engine(catalog_repository=repository)
    engine.register_table(
        "txns",
        pd.DataFrame(
            {"txn_id": [1, 2], "status": ["failed", "open"]}
        ),
        primary_key="txn_id",
        primary_key_type="INT64",
    )
    engine.execute(
        """
        CREATE CONTEXT atomic_ctx ON txn_id
        WITH (materialized = TRUE, storage = 'set')
        AS SELECT txn_id FROM txns WHERE status = 'failed';
        """
    )
    engine.execute("REFRESH CONTEXT atomic_ctx;")
    engine.register_table(
        "txns",
        pd.DataFrame(
            {"txn_id": [1, 2], "status": ["open", "failed"]}
        ),
        primary_key="txn_id",
        primary_key_type="INT64",
    )

    entered = threading.Event()
    release = threading.Event()
    original_promote = repository.promote_snapshot

    def blocked_promote(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return original_promote(*args, **kwargs)

    monkeypatch.setattr(repository, "promote_snapshot", blocked_promote)
    failure = []

    def refresh():
        try:
            engine.execute("REFRESH CONTEXT atomic_ctx;")
        except Exception as exc:  # pragma: no cover - diagnostic capture
            failure.append(exc)

    worker = threading.Thread(target=refresh)
    worker.start()
    assert entered.wait(timeout=10)

    # The store remains readable and exposes the complete old snapshot while
    # the durable transaction is blocked.
    assert engine.membership.members("atomic_ctx") == {1}

    release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert failure == []
    new = engine.execute(
        "SELECT txn_id FROM txns "
        "WHERE CONTEXT IN (atomic_ctx) ORDER BY txn_id;"
    ).to_pandas()
    assert list(new["txn_id"]) == [2]
    assert engine.membership.members("atomic_ctx") == {2}


def test_simultaneous_refreshes_serialize_per_context(
    tmp_path, monkeypatch
):
    conn = _migrated_connection(tmp_path / "serialized-catalog.db")
    repository = SQLiteContextRuntimeRepository(conn)
    engine = cql.Engine(catalog_repository=repository)
    engine.register_table(
        "txns",
        pd.DataFrame({"txn_id": [1, 2]}),
        primary_key="txn_id",
        primary_key_type="INT64",
    )
    engine.execute(
        """
        CREATE CONTEXT serialized_ctx ON txn_id
        WITH (materialized = TRUE, storage = 'set')
        AS SELECT txn_id FROM txns;
        """
    )
    engine.execute("REFRESH CONTEXT serialized_ctx;")

    active = 0
    max_active = 0
    counter_lock = threading.Lock()
    original_promote = repository.promote_snapshot

    def measured_promote(*args, **kwargs):
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return original_promote(*args, **kwargs)
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(repository, "promote_snapshot", measured_promote)
    workers = [
        threading.Thread(
            target=lambda: engine.execute(
                "REFRESH CONTEXT serialized_ctx;"
            )
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert not worker.is_alive()
    assert max_active == 1
    assert engine.membership.get_snapshot(
        "serialized_ctx"
    ).version == 3


def test_corrupt_payload_fails_closed_with_e201(tmp_path):
    conn = _migrated_connection(tmp_path / "catalog.db")
    repository = SQLiteContextRuntimeRepository(conn)
    engine = cql.Engine(catalog_repository=repository)
    engine.register_table(
        "txns",
        pd.DataFrame({"txn_id": [1]}),
        primary_key="txn_id",
        primary_key_type="INT64",
    )
    engine.execute(
        """
        CREATE CONTEXT corruptible ON txn_id
        WITH (materialized = TRUE, storage = 'set')
        AS SELECT txn_id FROM txns;
        """
    )
    engine.execute("REFRESH CONTEXT corruptible;")
    entry = engine._catalog.get_context("corruptible")
    conn.execute(
        """
        UPDATE context_snapshot_payloads
        SET membership_blob = X'0001'
        WHERE context_id = ?
        """,
        (entry.context_id,),
    )
    conn.commit()

    with pytest.raises(ValueError, match="E201"):
        cql.Engine(
            catalog_repository=SQLiteContextRuntimeRepository(conn)
        )


def test_schema_v2_migration_backfills_one_stable_context_id():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    for name in ("001_initial.sql", "002_context_snapshots.sql"):
        conn.executescript((_migrations() / name).read_text())
    conn.executemany(
        """
        INSERT INTO contexts (
            name, namespace, version, definition_text, entity_key
        ) VALUES ('risk', 'default', ?, 'SELECT id FROM facts', 'id')
        """,
        [(1,), (2,)],
    )
    conn.executescript(
        (_migrations() / "003_context_runtime_state.sql").read_text()
    )
    repository = SQLiteContextRuntimeRepository(conn)
    ids = {
        row[0]
        for row in conn.execute(
            "SELECT context_id FROM contexts WHERE name = 'risk'"
        )
    }
    assert len(ids) == 1
    assert next(iter(ids))
    assert [entry.version for entry in repository.load_contexts()] == [2]


def test_synchronizer_watermark_and_idempotency_survive_restart():
    conn = _migrated_connection()
    state = SQLiteSynchronizerStateRepository(conn)
    service = MockDeepSeeService(members={1: 0.5}, page_size=10)
    client = DeepSeeClient(service)
    store = SetMembershipStore()

    first = DeepSeeSynchronizer(
        client, store, "deepsee-risk", state_repository=state
    )
    first.bootstrap()
    event = service.add_member(2, 0.8)
    first.sync_once()

    restarted = DeepSeeSynchronizer(
        client, store, "deepsee-risk", state_repository=state
    )
    assert restarted.committed_watermark == event.watermark
    assert event.event_id in restarted._seen_event_ids
    report = restarted.sync_once()
    assert report.applied_total == 0
    assert store.members("deepsee-risk") == {1, 2}
