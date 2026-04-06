"""SQLite database connection and migration management."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_connection: sqlite3.Connection | None = None


def init_db(db_path: str = "cql_catalog.db") -> sqlite3.Connection:
    """Initialize the SQLite database, run pending migrations, and return the connection."""
    global _connection

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    _run_migrations(conn)

    _connection = conn
    logger.info("Database initialized: %s", db_path)
    return conn


def get_db() -> sqlite3.Connection:
    """Return the active database connection."""
    if _connection is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _connection


def close_db() -> None:
    """Close the database connection."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
        logger.info("Database connection closed")


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any pending SQL migration scripts in order."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )

    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current_version = row[0] if row[0] is not None else 0

    migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    for mf in migration_files:
        file_version = int(mf.stem.split("_")[0])
        if file_version > current_version:
            logger.info("Applying migration %s", mf.name)
            sql = mf.read_text()
            conn.executescript(sql)
            logger.info("Migration %s applied", mf.name)

    conn.commit()
