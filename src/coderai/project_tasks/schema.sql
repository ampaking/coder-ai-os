PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
  project_hash TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  project_hash TEXT NOT NULL REFERENCES projects(project_hash) ON DELETE CASCADE,
  title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 240),
  summary TEXT NOT NULL DEFAULT '' CHECK(length(summary) <= 2000),
  theme TEXT NOT NULL DEFAULT '' CHECK(length(theme) <= 120),
  status TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1),
  source TEXT NOT NULL DEFAULT 'agent',
  estimated_minutes INTEGER CHECK(estimated_minutes IS NULL OR estimated_minutes >= 0),
  actual_minutes INTEGER CHECK(actual_minutes IS NULL OR actual_minutes >= 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS tasks_project_status ON tasks(project_hash, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  project_hash TEXT NOT NULL REFERENCES projects(project_hash) ON DELETE CASCADE,
  task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
  agent_name TEXT NOT NULL CHECK(length(agent_name) BETWEEN 1 AND 80),
  agent_version TEXT NOT NULL DEFAULT '' CHECK(length(agent_version) <= 80),
  model_name TEXT NOT NULL DEFAULT '' CHECK(length(model_name) <= 120),
  native_session_id TEXT CHECK(native_session_id IS NULL OR length(native_session_id) <= 240),
  resume_supported INTEGER NOT NULL DEFAULT 0 CHECK(resume_supported IN (0, 1)),
  started_at TEXT NOT NULL,
  ended_at TEXT
);

CREATE INDEX IF NOT EXISTS sessions_project_started ON sessions(project_hash, started_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS sessions_native
  ON sessions(project_hash, agent_name, native_session_id)
  WHERE native_session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS task_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL UNIQUE,
  project_hash TEXT NOT NULL REFERENCES projects(project_hash) ON DELETE CASCADE,
  task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
  session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  event_type TEXT NOT NULL,
  summary TEXT NOT NULL CHECK(length(summary) BETWEEN 1 AND 2000),
  evidence_json TEXT NOT NULL DEFAULT '[]' CHECK(length(evidence_json) <= 12000),
  confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1),
  occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_project_time ON task_events(project_hash, occurred_at DESC);
CREATE INDEX IF NOT EXISTS events_task_time ON task_events(task_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS task_links (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  related_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  link_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(task_id, related_task_id, link_type),
  CHECK(task_id <> related_task_id)
);

CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  summary TEXT NOT NULL CHECK(length(summary) BETWEEN 1 AND 2000),
  reason TEXT NOT NULL DEFAULT '' CHECK(length(reason) <= 4000),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blockers (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  summary TEXT NOT NULL CHECK(length(summary) BETWEEN 1 AND 2000),
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS validations (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  category TEXT NOT NULL CHECK(length(category) BETWEEN 1 AND 80),
  command_summary TEXT NOT NULL DEFAULT '' CHECK(length(command_summary) <= 500),
  outcome TEXT NOT NULL,
  duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
  occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS blockers_task_status_time
ON blockers(task_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS validations_task_time
ON validations(task_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY,
  project_hash TEXT NOT NULL REFERENCES projects(project_hash) ON DELETE CASCADE,
  task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
  event_id TEXT REFERENCES task_events(id) ON DELETE SET NULL,
  source TEXT NOT NULL CHECK(length(source) BETWEEN 1 AND 80),
  kind TEXT NOT NULL CHECK(length(kind) BETWEEN 1 AND 80),
  scope TEXT NOT NULL DEFAULT 'project' CHECK(length(scope) BETWEEN 1 AND 80),
  outcome TEXT NOT NULL CHECK(outcome IN ('passed','failed','warning','information','unknown')),
  summary TEXT NOT NULL CHECK(length(summary) BETWEEN 1 AND 500),
  metric_value REAL,
  metric_unit TEXT NOT NULL DEFAULT '' CHECK(length(metric_unit) <= 40),
  confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1),
  occurred_at TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  UNIQUE(project_hash, event_id, kind)
);

CREATE INDEX IF NOT EXISTS observations_project_source_time
ON observations(project_hash, source, occurred_at DESC);

CREATE INDEX IF NOT EXISTS observations_project_kind_outcome_time
ON observations(project_hash, kind, outcome, occurred_at DESC);

CREATE INDEX IF NOT EXISTS observations_project_time
ON observations(project_hash, occurred_at DESC);

CREATE TABLE IF NOT EXISTS collector_receipts (
  id TEXT PRIMARY KEY,
  project_hash TEXT NOT NULL REFERENCES projects(project_hash) ON DELETE CASCADE,
  collector TEXT NOT NULL CHECK(length(collector) BETWEEN 1 AND 80),
  trigger_kind TEXT NOT NULL CHECK(trigger_kind IN ('agent-hook','validation-hook','git-hook','manual','service')),
  status TEXT NOT NULL CHECK(status IN ('success','no_change','partial','failed')),
  observed_count INTEGER NOT NULL DEFAULT 0 CHECK(observed_count >= 0),
  accepted_count INTEGER NOT NULL DEFAULT 0 CHECK(accepted_count >= 0),
  duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK(duplicate_count >= 0),
  rejected_count INTEGER NOT NULL DEFAULT 0 CHECK(rejected_count >= 0),
  error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code) <= 80),
  contract_version INTEGER NOT NULL DEFAULT 1 CHECK(contract_version = 1),
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  CHECK(accepted_count + duplicate_count + rejected_count <= observed_count)
);

CREATE INDEX IF NOT EXISTS collector_receipts_project_collector_time
ON collector_receipts(project_hash, collector, finished_at DESC);

CREATE INDEX IF NOT EXISTS collector_receipts_project_status_time
ON collector_receipts(project_hash, status, finished_at DESC);

CREATE INDEX IF NOT EXISTS collector_receipts_project_time
ON collector_receipts(project_hash, finished_at DESC);

CREATE TABLE IF NOT EXISTS corrections (
  id TEXT PRIMARY KEY,
  task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
  field_name TEXT NOT NULL CHECK(length(field_name) BETWEEN 1 AND 80),
  previous_value TEXT NOT NULL DEFAULT '' CHECK(length(previous_value) <= 2000),
  corrected_value TEXT NOT NULL CHECK(length(corrected_value) <= 2000),
  reason TEXT NOT NULL DEFAULT '' CHECK(length(reason) <= 2000),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commits (
  hash TEXT PRIMARY KEY,
  project_hash TEXT NOT NULL REFERENCES projects(project_hash) ON DELETE CASCADE,
  parent_hashes TEXT NOT NULL DEFAULT '',
  committed_at TEXT NOT NULL,
  additions INTEGER NOT NULL DEFAULT 0,
  deletions INTEGER NOT NULL DEFAULT 0,
  file_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS task_commits (
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  commit_hash TEXT NOT NULL REFERENCES commits(hash) ON DELETE CASCADE,
  confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
  evidence_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','confirmed','rejected')),
  corrected INTEGER NOT NULL DEFAULT 0 CHECK(corrected IN (0, 1)),
  PRIMARY KEY(task_id, commit_hash)
);

CREATE TABLE IF NOT EXISTS ideas (
  id TEXT PRIMARY KEY,
  project_hash TEXT NOT NULL REFERENCES projects(project_hash) ON DELETE CASCADE,
  task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
  title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 240),
  reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 2000),
  evidence_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'proposed',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY,
  project_hash TEXT NOT NULL REFERENCES projects(project_hash) ON DELETE CASCADE,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  facts_json TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY,
  project_hash TEXT NOT NULL REFERENCES projects(project_hash) ON DELETE CASCADE,
  fingerprint TEXT NOT NULL CHECK(length(fingerprint) BETWEEN 1 AND 160),
  kind TEXT NOT NULL CHECK(kind IN ('weekly-progress','daily-reflection','attention')),
  severity TEXT NOT NULL CHECK(severity IN ('information','warning')),
  title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 160),
  body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 500),
  evidence_json TEXT NOT NULL DEFAULT '[]' CHECK(length(evidence_json) <= 4000),
  state TEXT NOT NULL DEFAULT 'unread' CHECK(state IN ('unread','read','dismissed')),
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  delivered_at TEXT,
  UNIQUE(project_hash, fingerprint)
);

CREATE INDEX IF NOT EXISTS notifications_project_state_time
ON notifications(project_hash, state, updated_at DESC);
