-- Post-trade Roaring contexts: snapshot metadata and membership history
-- (contextql docs/plans/post-trade-roaring-contexts.md section 7.2)

CREATE TABLE IF NOT EXISTS context_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    storage_kind TEXT NOT NULL DEFAULT 'roaring',
    bitmap_location TEXT,
    score_location TEXT,
    member_count INTEGER NOT NULL DEFAULT 0,
    serialized_bytes INTEGER,
    computed_at TEXT NOT NULL,
    data_as_of TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    definition_hash TEXT,
    source_watermark TEXT,
    state TEXT NOT NULL DEFAULT 'building',
    error_detail TEXT,
    UNIQUE(context_id, version)
);

CREATE INDEX IF NOT EXISTS idx_context_snapshots_current
    ON context_snapshots (context_id, state);

-- Shared append-oriented membership history: one row per change event,
-- never one row per current member (DECISIONS.md CS-5).
CREATE TABLE IF NOT EXISTS context_membership_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id TEXT NOT NULL,
    transaction_id INTEGER NOT NULL,
    change_type TEXT NOT NULL CHECK (
        change_type IN ('added', 'removed', 'score_changed')
    ),
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    effective_at TEXT NOT NULL,
    context_version INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'native',
    evidence_ref TEXT,
    previous_score REAL,
    new_score REAL
);

CREATE INDEX IF NOT EXISTS idx_membership_history_context
    ON context_membership_history (context_id, context_version);
CREATE INDEX IF NOT EXISTS idx_membership_history_effective
    ON context_membership_history (context_id, effective_at);

INSERT OR IGNORE INTO schema_version (version) VALUES (2);
