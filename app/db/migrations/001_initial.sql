-- v0.3 Control Plane Foundation schema

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'default',
    version INTEGER NOT NULL DEFAULT 1,
    definition_text TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    has_score INTEGER NOT NULL DEFAULT 0,
    score_column TEXT,
    description TEXT,
    tags TEXT,
    classification TEXT DEFAULT 'internal',
    lifecycle_state TEXT NOT NULL DEFAULT 'draft',
    dependency_refs TEXT,
    provider_refs TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_validated_at TEXT,
    last_executed_at TEXT,
    freshness_metadata TEXT,
    UNIQUE(name, namespace, version)
);

CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'default',
    provider_type TEXT NOT NULL,
    endpoint TEXT,
    credentials_ref TEXT,
    timeout_ms INTEGER DEFAULT 30000,
    health_state TEXT DEFAULT 'unknown',
    entity_key_type TEXT,
    resource_shape TEXT,
    trust_tier TEXT DEFAULT 'standard',
    enabled INTEGER NOT NULL DEFAULT 1,
    registered_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_success_at TEXT,
    last_failure_at TEXT,
    UNIQUE(name, namespace)
);

CREATE TABLE IF NOT EXISTS identity_maps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT 'default',
    source_system TEXT NOT NULL,
    source_entity_path TEXT NOT NULL,
    target_system TEXT NOT NULL,
    target_entity_path TEXT NOT NULL,
    matching_mode TEXT DEFAULT 'exact',
    confidence REAL DEFAULT 1.0,
    description TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, namespace)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    event_type TEXT NOT NULL,
    actor TEXT DEFAULT 'system',
    namespace TEXT DEFAULT 'default',
    resource_type TEXT,
    resource_name TEXT,
    detail TEXT,
    trace_id TEXT
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
