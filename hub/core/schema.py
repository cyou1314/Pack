"""Canonical SQLite base schema for the Pack Hub.

Incremental upgrades remain in hub.init() until the migration sequence has
explicit fixtures and a stable migration runner.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS members(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, role TEXT NOT NULL,
  token_hash TEXT NOT NULL, profile TEXT, last_heartbeat REAL, created_at REAL NOT NULL,
  avatar TEXT);
CREATE TABLE IF NOT EXISTS rooms(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL, author_id INTEGER NOT NULL,
  kind TEXT NOT NULL, body TEXT NOT NULL, task_id INTEGER, created_at REAL NOT NULL,
  deleted_at REAL, edited_at REAL);
CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, id);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
  created_by INTEGER NOT NULL, assignee INTEGER, status TEXT NOT NULL,
  claim_token TEXT, lease_expires REAL, attempts INTEGER NOT NULL DEFAULT 0,
  result TEXT, envelope TEXT, review_note TEXT, fail_class TEXT, next_claim_at REAL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS room_summaries(
  room_id INTEGER PRIMARY KEY, data TEXT NOT NULL,
  updated_at REAL NOT NULL, updated_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS desktop_lock(
  id INTEGER PRIMARY KEY CHECK(id=1),
  holder TEXT, task_id INTEGER, acquired_at REAL);
CREATE TABLE IF NOT EXISTS task_templates(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, title_tpl TEXT NOT NULL,
  body_tpl TEXT NOT NULL, assignee TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL, updated_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approval_rules(
  id INTEGER PRIMARY KEY, action TEXT NOT NULL DEFAULT '', target TEXT NOT NULL DEFAULT '',
  effect TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(action, target));
CREATE TABLE IF NOT EXISTS egress_rules(
  id INTEGER PRIMARY KEY, pattern TEXT NOT NULL UNIQUE, effect TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS review_log(
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, target_url TEXT NOT NULL,
  owner_quote TEXT NOT NULL, member_desc TEXT NOT NULL, verdict TEXT NOT NULL,
  reviewer TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, member_id INTEGER NOT NULL,
  fence INTEGER NOT NULL, claim_token TEXT, status TEXT NOT NULL DEFAULT 'running',
  outcome TEXT, detail TEXT, started_at REAL NOT NULL, ended_at REAL,
  UNIQUE(task_id, fence));
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_one_running ON runs(task_id) WHERE status='running';
CREATE TABLE IF NOT EXISTS member_memory(
  member TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL, version_id INTEGER NOT NULL, updated_at REAL NOT NULL,
  PRIMARY KEY (member, scope, key));
CREATE TABLE IF NOT EXISTS member_memory_versions(
  id INTEGER PRIMARY KEY, member TEXT NOT NULL, scope TEXT NOT NULL,
  key TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, author TEXT NOT NULL,
  origin TEXT NOT NULL, source_refs TEXT NOT NULL DEFAULT '[]',
  size INTEGER NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_memver ON member_memory_versions(member, scope, key, id DESC);
CREATE TABLE IF NOT EXISTS task_transfers(
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, from_member TEXT NOT NULL,
  to_member TEXT NOT NULL, reason TEXT, actor TEXT NOT NULL, created_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', accepted_at REAL, expires_at REAL);
CREATE INDEX IF NOT EXISTS idx_transfers_task ON task_transfers(task_id, id);
CREATE TABLE IF NOT EXISTS skills(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT NOT NULL DEFAULT '',
  current_version INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS skill_versions(
  id INTEGER PRIMARY KEY, skill_id INTEGER NOT NULL, version INTEGER NOT NULL,
  body TEXT NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL,
  UNIQUE(skill_id, version));
CREATE INDEX IF NOT EXISTS idx_skillver ON skill_versions(skill_id, version DESC);
CREATE TABLE IF NOT EXISTS member_skills(
  member_id INTEGER NOT NULL, skill_id INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (member_id, skill_id));
CREATE TABLE IF NOT EXISTS attachments(
  id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, mime TEXT NOT NULL,
  size INTEGER NOT NULL, original_name TEXT NOT NULL DEFAULT '',
  uploader_id INTEGER NOT NULL, room_id INTEGER NOT NULL,
  message_id INTEGER, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_attach_msg ON attachments(message_id);
CREATE INDEX IF NOT EXISTS idx_attach_room ON attachments(room_id, id);
CREATE TABLE IF NOT EXISTS pair_codes(
  id INTEGER PRIMARY KEY, code_hash TEXT UNIQUE NOT NULL,
  member_name TEXT NOT NULL, provider TEXT NOT NULL DEFAULT '',
  bound_room_id INTEGER NOT NULL, display_name TEXT,
  reclaim INTEGER NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
  redeemed_member_id INTEGER, redeemed_at REAL, revoked_at REAL,
  fail_count INTEGER NOT NULL DEFAULT 0, locked_at REAL);
CREATE TABLE IF NOT EXISTS member_read_cursors(
  member_id INTEGER NOT NULL, room_id INTEGER NOT NULL,
  cursor_id INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (member_id, room_id));
CREATE TABLE IF NOT EXISTS hub_settings(
  key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS message_op_keys(
  member_id INTEGER NOT NULL, op_key TEXT NOT NULL, message_id INTEGER,
  created_at REAL NOT NULL, PRIMARY KEY (member_id, op_key));
CREATE TABLE IF NOT EXISTS pair_attempts(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, success INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_pair_attempts_ts ON pair_attempts(ts);
"""
