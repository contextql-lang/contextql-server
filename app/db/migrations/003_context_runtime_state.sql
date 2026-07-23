-- Durable ContextQL DDL, snapshots, history, and connector synchronization.

ALTER TABLE contexts ADD COLUMN context_id TEXT;
ALTER TABLE contexts ADD COLUMN definition_hash TEXT;
ALTER TABLE contexts ADD COLUMN raw_ddl TEXT;
ALTER TABLE contexts ADD COLUMN materialization_json TEXT;
ALTER TABLE contexts ADD COLUMN current_snapshot_version INTEGER;
ALTER TABLE contexts ADD COLUMN last_refreshed_at TEXT;
ALTER TABLE contexts ADD COLUMN data_as_of TEXT;
ALTER TABLE contexts ADD COLUMN last_refresh_error TEXT;
ALTER TABLE contexts ADD COLUMN history_available_from TEXT;
ALTER TABLE contexts ADD COLUMN dropped_at TEXT;
ALTER TABLE contexts ADD COLUMN definition_sql TEXT;
ALTER TABLE contexts ADD COLUMN entity_key_type TEXT;
ALTER TABLE contexts ADD COLUMN score_expression TEXT;
ALTER TABLE contexts ADD COLUMN composition_json TEXT;
ALTER TABLE contexts ADD COLUMN temporal_column TEXT;
ALTER TABLE contexts ADD COLUMN temporal_granularity TEXT;
ALTER TABLE contexts ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'native';

CREATE UNIQUE INDEX IF NOT EXISTS idx_contexts_runtime_version
    ON contexts (context_id, version);
CREATE INDEX IF NOT EXISTS idx_contexts_runtime_name
    ON contexts (namespace, name, dropped_at, version);

ALTER TABLE context_snapshots ADD COLUMN definition_version INTEGER;
ALTER TABLE context_snapshots ADD COLUMN membership_sha256 TEXT;
ALTER TABLE context_snapshots ADD COLUMN score_sha256 TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_context_snapshots_one_current
    ON context_snapshots (context_id)
    WHERE state = 'current';

CREATE TABLE IF NOT EXISTS context_snapshot_payloads (
    context_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    membership_blob BLOB NOT NULL,
    score_blob BLOB,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (context_id, version)
);

ALTER TABLE context_membership_history ADD COLUMN definition_version INTEGER;
ALTER TABLE context_membership_history ADD COLUMN definition_hash TEXT;
ALTER TABLE context_membership_history ADD COLUMN event_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_membership_history_event
    ON context_membership_history (context_id, event_id)
    WHERE event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS context_sync_state (
    context_id TEXT PRIMARY KEY,
    committed_watermark TEXT,
    ordering_boundary TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS context_sync_events (
    context_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    watermark TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (context_id, event_id)
);

INSERT OR IGNORE INTO schema_version (version) VALUES (3);
