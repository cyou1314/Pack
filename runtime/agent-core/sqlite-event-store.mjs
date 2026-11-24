import {mkdirSync, chmodSync} from "node:fs";
import {dirname, resolve} from "node:path";
import {DatabaseSync} from "node:sqlite";

function requiredText(value, field, maxLength = 160) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > maxLength) {
    throw new TypeError("invalid_" + field);
  }
  return value.trim();
}

function normalizeEvent(event) {
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    throw new TypeError("invalid_agent_event");
  }
  const eventId = requiredText(event.eventId, "event_id");
  const serialized = JSON.stringify(event);
  if (typeof serialized !== "string") throw new TypeError("invalid_agent_event");
  return {eventId, serialized};
}

function normalizeLimit(limit) {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 1000) {
    throw new TypeError("invalid_event_limit");
  }
  return limit;
}

/**
 * Crash-safe local event outbox.
 *
 * Each append is committed to SQLite in WAL mode with synchronous=FULL before
 * it resolves. Delivery acknowledgement is separate from persistence, so a
 * process restart can safely replay pending rows. The caller owns this DB file.
 */
export class SqliteEventStore {
  #db;
  #insert;
  #select;
  #pending;
  #ack;
  #counts;
  #closed = false;

  constructor({databasePath, busyTimeoutMs = 5000} = {}) {
    const filePath = requiredText(databasePath, "database_path", 4096);
    if (!Number.isSafeInteger(busyTimeoutMs) || busyTimeoutMs < 0 || busyTimeoutMs > 60000) {
      throw new TypeError("invalid_busy_timeout");
    }

    const absolutePath = resolve(filePath);
    mkdirSync(dirname(absolutePath), {recursive: true, mode: 0o700});
    this.#db = new DatabaseSync(absolutePath);
    try {
      chmodSync(absolutePath, 0o600);
      this.#db.exec(`
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = FULL;
        PRAGMA foreign_keys = ON;
        PRAGMA secure_delete = ON;
        PRAGMA busy_timeout = ${busyTimeoutMs};
        PRAGMA wal_autocheckpoint = 1000;
        CREATE TABLE IF NOT EXISTS agent_event_outbox (
          outbox_seq INTEGER PRIMARY KEY AUTOINCREMENT,
          event_id TEXT NOT NULL UNIQUE,
          event_json TEXT NOT NULL,
          inserted_at TEXT NOT NULL,
          delivered_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_agent_event_outbox_pending
          ON agent_event_outbox(delivered_at, outbox_seq);
      `);
    } catch (error) {
      try { this.#db.close(); } catch {}
      this.#closed = true;
      throw error;
    }
    this.#insert = this.#db.prepare(`
      INSERT OR IGNORE INTO agent_event_outbox (event_id, event_json, inserted_at)
      VALUES (?, ?, ?)
    `);
    this.#select = this.#db.prepare(
      "SELECT event_json FROM agent_event_outbox WHERE event_id = ?"
    );
    this.#pending = this.#db.prepare(`
      SELECT event_json FROM agent_event_outbox
      WHERE delivered_at IS NULL ORDER BY outbox_seq ASC LIMIT ?
    `);
    this.#ack = this.#db.prepare(`
      UPDATE agent_event_outbox SET delivered_at = ?
      WHERE event_id = ? AND delivered_at IS NULL
    `);
    this.#counts = this.#db.prepare(`
      SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN delivered_at IS NULL THEN 1 ELSE 0 END) AS pending
      FROM agent_event_outbox
    `);
  }

  async append(event) {
    this.#assertOpen();
    const {eventId, serialized} = normalizeEvent(event);
    this.#db.exec("BEGIN IMMEDIATE");
    try {
      const result = this.#insert.run(eventId, serialized, new Date().toISOString());
      if (Number(result.changes) === 0) {
        const existing = this.#select.get(eventId)?.event_json;
        if (existing !== serialized) throw new Error("event_id_payload_conflict");
      }
      this.#db.exec("COMMIT");
      return {inserted: Number(result.changes) === 1};
    } catch (error) {
      try { this.#db.exec("ROLLBACK"); } catch {}
      throw error;
    }
  }

  async listPending({limit = 100} = {}) {
    this.#assertOpen();
    return this.#pending.all(normalizeLimit(limit)).map((row) => JSON.parse(row.event_json));
  }

  async markDelivered(eventIds) {
    this.#assertOpen();
    if (!Array.isArray(eventIds) || eventIds.length > 1000) {
      throw new TypeError("invalid_event_ids");
    }
    const ids = [...new Set(eventIds.map((id) => requiredText(id, "event_id")))];
    if (!ids.length) return {updated: 0};

    const deliveredAt = new Date().toISOString();
    this.#db.exec("BEGIN IMMEDIATE");
    try {
      let updated = 0;
      for (const eventId of ids) updated += Number(this.#ack.run(deliveredAt, eventId).changes);
      this.#db.exec("COMMIT");
      return {updated};
    } catch (error) {
      try { this.#db.exec("ROLLBACK"); } catch {}
      throw error;
    }
  }

  async getStats() {
    this.#assertOpen();
    const row = this.#counts.get();
    return {total: Number(row.total), pending: Number(row.pending || 0)};
  }

  close() {
    if (this.#closed) return;
    try { this.#db.exec("PRAGMA wal_checkpoint(TRUNCATE)"); }
    finally {
      this.#db.close();
      this.#closed = true;
    }
  }

  #assertOpen() {
    if (this.#closed) throw new Error("event_store_closed");
  }
}
