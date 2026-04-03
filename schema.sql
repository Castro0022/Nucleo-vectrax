-- ==========================================================================
-- DEPRECATED — Reference only.
--
-- These tables are now created by  vectrax/db.py → init_db()  which is the
-- single entry point for all database initialisation.  Do NOT run this file
-- directly.  It is kept for documentation purposes.
--
-- See: vectrax/db.py
-- ==========================================================================

PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS queries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  prompt TEXT NOT NULL,
  topic TEXT DEFAULT 'general',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  final TEXT,
  divergence REAL DEFAULT 0.0,
  consensus_mode INTEGER DEFAULT 0,
  routing_mode TEXT DEFAULT '',
  routing_reason TEXT DEFAULT '',
  confidence REAL DEFAULT 0.0,
  risk_level TEXT DEFAULT '',
  estimated_savings REAL DEFAULT 0.0,
  providers_called TEXT DEFAULT '',
  fallback_used INTEGER DEFAULT 0,
  star_id TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS responses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query_id INTEGER NOT NULL REFERENCES queries(id),
  provider TEXT NOT NULL,
  content TEXT,
  latency_ms INTEGER DEFAULT 0,
  error TEXT DEFAULT ''
);

-- Pesos adaptativos (Bayes) por tópico: mean = alpha/(alpha+beta)
CREATE TABLE IF NOT EXISTS provider_weights (
  provider TEXT NOT NULL,
  topic TEXT NOT NULL,
  alpha REAL DEFAULT 1.0,
  beta  REAL DEFAULT 1.0,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(provider, topic)
);

CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  query_id INTEGER NOT NULL REFERENCES queries(id),
  topic TEXT NOT NULL,
  chosen_provider TEXT NOT NULL,
  reason TEXT DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
