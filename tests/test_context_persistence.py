"""Restart durability for executable DDL and snapshot runtime state."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import contextql as cql
from contextql.semantic import TableCatalogEntry

from app.repositories import SQLiteContextRuntimeRepository


def _migrated_connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    migrations = (
        Path(__file__).parents[1] / "app" / "db" / "migrations"
    )
    for migration in sorted(migrations.glob("*.sql")):
        conn.executescript(migration.read_text())
    conn.commit()
    return conn


def _register_table_metadata(engine) -> None:
    engine._catalog.tables["txns"] = TableCatalogEntry(
        name="txns",
        primary_key_name="txn_id",
        primary_key_type="INT64",
    )


def test_context_snapshot_and_scores_survive_restart(tmp_path):
    catalog_path = tmp_path / "catalog.db"
    duckdb_path = tmp_path / "facts.duckdb"
    conn = _migrated_connection(catalog_path)
    repository = SQLiteContextRuntimeRepository(conn)

    first = cql.Engine(
        database=str(duckdb_path),
        catalog_repository=repository,
    )
    first._adapter.conn.execute(
        """
        CREATE TABLE txns AS
        SELECT * FROM (VALUES
            (1, 'failed', 0.9),
            (2, 'open', 0.2),
            (3, 'failed', 0.7)
        ) AS t(txn_id, status, risk)
        """
    )
    _register_table_metadata(first)
    first.execute(
        """
        CREATE CONTEXT durable_failed ON txn_id SCORE risk
        WITH (materialized = TRUE, storage = 'set', history = TRUE)
        AS SELECT txn_id, risk FROM txns WHERE status = 'failed';
        """
    )
    first.execute("REFRESH CONTEXT durable_failed;")
    before = first._catalog.get_context("durable_failed")
    before_id = before.context_id
    before_version = before.current_snapshot_version
    first._adapter.conn.close()

    restarted_repository = SQLiteContextRuntimeRepository(conn)
    second = cql.Engine(
        database=str(duckdb_path),
        catalog_repository=restarted_repository,
    )
    _register_table_metadata(second)
    after = second._catalog.get_context("durable_failed")
    assert after.context_id == before_id
    assert after.current_snapshot_version == before_version
    result = second.execute(
        "SELECT txn_id, CONTEXT_SCORE() AS score FROM txns "
        "WHERE CONTEXT IN (durable_failed) "
        "ORDER BY txn_id;"
    ).to_pandas()
    assert list(result["txn_id"]) == [1, 3]
    assert list(result["score"]) == [0.9, 0.7]
    assert len(second._executor.history.events(before_id)) == 2
    second._adapter.conn.close()
    conn.close()


def test_temporal_history_survives_restart(tmp_path, monkeypatch):
    import contextql.context_ddl as ddl_module

    catalog_path = tmp_path / "temporal-catalog.db"
    duckdb_path = tmp_path / "temporal-facts.duckdb"
    conn = _migrated_connection(catalog_path)
    repository = SQLiteContextRuntimeRepository(conn)
    engine = cql.Engine(
        database=str(duckdb_path),
        catalog_repository=repository,
    )
    engine._adapter.conn.execute(
        """
        CREATE TABLE txns (
            txn_id BIGINT,
            active BOOLEAN,
            risk DOUBLE,
            event_at TIMESTAMPTZ
        )
        """
    )
    _register_table_metadata(engine)
    clock = [
        datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    ]
    monkeypatch.setattr(ddl_module, "_now", lambda: clock[0])
    engine.execute(
        """
        CREATE CONTEXT temporal_risk ON txn_id SCORE risk
        TEMPORAL (event_at, SECOND)
        WITH (materialized = TRUE, storage = 'set', history = TRUE)
        AS SELECT txn_id, risk, event_at FROM txns WHERE active = TRUE;
        """
    )

    engine._adapter.conn.execute(
        """
        INSERT INTO txns VALUES
          (1, TRUE, 0.2, TIMESTAMPTZ '2026-07-01 00:00:00+00'),
          (2, TRUE, 0.4, TIMESTAMPTZ '2026-07-01 00:00:00+00')
        """
    )
    engine.execute("REFRESH CONTEXT temporal_risk;")

    clock[0] = datetime(2026, 7, 1, 1, 0, tzinfo=timezone.utc)
    engine._adapter.conn.execute(
        """
        UPDATE txns SET risk = 0.8,
          event_at = TIMESTAMPTZ '2026-07-01 01:00:00+00'
        WHERE txn_id = 1
        """
    )
    engine.execute("REFRESH CONTEXT temporal_risk;")

    clock[0] = datetime(2026, 7, 1, 2, 0, tzinfo=timezone.utc)
    engine._adapter.conn.execute(
        "UPDATE txns SET active = FALSE WHERE txn_id = 2"
    )
    engine.execute("REFRESH CONTEXT temporal_risk;")

    clock[0] = datetime(2026, 7, 1, 3, 0, tzinfo=timezone.utc)
    engine._adapter.conn.execute(
        """
        INSERT INTO txns VALUES
          (3, TRUE, 0.6, TIMESTAMPTZ '2026-07-01 03:00:00+00')
        """
    )
    engine.execute("REFRESH CONTEXT temporal_risk;")

    def query(target_engine, qualifier):
        return target_engine.execute(
            "SELECT txn_id, CONTEXT_SCORE() AS score FROM txns "
            f"WHERE CONTEXT IN (temporal_risk {qualifier}) "
            "ORDER BY txn_id;"
        ).to_pandas()

    at_t1 = query(
        engine, "AT '2026-07-01T01:00:00+00:00'"
    )
    assert dict(zip(at_t1["txn_id"], at_t1["score"])) == {
        1: 0.8,
        2: 0.4,
    }
    between = query(
        engine,
        "BETWEEN '2026-07-01T01:00:00+00:00' "
        "AND '2026-07-01T03:00:00+00:00'",
    )
    assert dict(zip(between["txn_id"], between["score"])) == {
        1: 0.8,
        2: 0.4,
        3: 0.6,
    }
    engine._adapter.conn.close()

    restarted = cql.Engine(
        database=str(duckdb_path),
        catalog_repository=SQLiteContextRuntimeRepository(conn),
    )
    _register_table_metadata(restarted)
    restarted_between = query(
        restarted,
        "BETWEEN '2026-07-01T01:00:00+00:00' "
        "AND '2026-07-01T03:00:00+00:00'",
    )
    assert restarted_between.to_dict("records") == between.to_dict("records")
    restarted._adapter.conn.close()
    conn.close()
