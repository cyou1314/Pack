import {closeSync, mkdirSync, chmodSync, openSync} from "node:fs";
import {dirname, resolve} from "node:path";
import {DatabaseSync} from "node:sqlite";

const MAX_RETRY_DELAY_MS = 24 * 60 * 60 * 1000;

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

function normalizeTimestamp(value, field) {
  if (!Number.isFinite(value) || value < 0) {
    throw new TypeError("invalid_" + field);
  }
  return value;
}

function normalizeErrorCode(value) {
  const code = requiredText(value, "delivery_error_code", 80);
  if (!/^[a-z0-9_.:-]+$/i.test(code)) {
    throw new TypeError("invalid_delivery_error_code");
  }
  return code;
}

/**
 * Crash-safe local event outbox.
 *
 * Each append is committed to SQLite in WAL mode with synchronous=FULL before
 * it resolves. Delivery acknowledgement is separate from persistence, so a
 * process restart can safely replay pending rows. Retry metadata is stored
 * beside each event; the first pending event blocks later events until due.
 */
export class SqliteEventStore {
  #db;
  #insert;
  #select;
  #pending;
  #ack;
  #retry;
  #deliveryState;
  #counts;
  #closed = false;

  constructor({databasePath, busyTimeoutMs = 5000} = {}) {
    const filePath = requiredText(databasePath, "database_path", 4096);
    if (!Number.isSafeInteger(busyTimeoutMs) || busyTimeoutMs < 0 || busyTimeoutMs > 60000) {
      throw new TypeError("invalid_busy_timeout");
    }

    const absolutePath = resolve(filePath);
    mkdirSync(dirname(absolutePath), {recursive: true, mode: 0o700});

    // Secure the main database before SQLite can create WAL/SHM sidecars.
    // mkdirSync's mode is ignored for an existing directory, and the caller's
    // umask may otherwise leave a newly-created database group/world-readable.
    const descriptor = openSync(absolutePath, "a", 0o600);
    try {
      chmodSync(absolutePath, 0o600);
    } finally {
      closeSync(descriptor);
    }

    this.#db = new DatabaseSync(absolutePath);
    try {
      this.#db.exec(
        "PRAGMA busy_timeout = " + busyTimeoutMs + ";\n" +
        "PRAGMA journal_mode = WAL;\n" +
        "PRAGMA synchronous = FULL;\n" +
        "PRAGMA foreign_keys = ON;\n" +
        "PRAGMA secure_delete = ON;\n" +
        "PRAGMA wal_autocheckpoint = 1000;"
      );
      this.#db.exec("BEGIN IMMEDIATE");
      try {
        this.#db.exec(
          "CREATE TABLE IF NOT EXISTS agent_event_outbox (\n" +
          "  outbox_seq INTEGER PRIMARY KEY AUTOINCREMENT,\n" +
          "  event_id TEXT NOT NULL UNIQUE,\n" +
          "  event_json TEXT NOT NULL,\n" +
          "  inserted_at TEXT NOT NULL,\n" +
          "  delivered_at TEXT,\n" +
          "  delivery_attempts INTEGER NOT NULL DEFAULT 0 CHECK(delivery_attempts >= 0),\n" +
          "  next_attempt_at REAL NOT NULL DEFAULT 0,\n" +
          "  last_error_code TEXT\n" +
          ")"
        );
        const columns = new Set(
          this.#db.prepare("PRAGMA table_info(agent_event_outbox)").all().map((row) => row.name)
        );
        if (!columns.has("delivery_attempts")) {
          this.#db.exec(
            "ALTER TABLE agent_event_outbox ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0"
          );
        }
        if (!columns.has("next_attempt_at")) {
          this.#db.exec(
            "ALTER TABLE agent_event_outbox ADD COLUMN next_attempt_at REAL NOT NULL DEFAULT 0"
          );
        }
        if (!columns.has("last_error_code")) {
          this.#db.exec(
            "ALTER TABLE agent_event_outbox ADD COLUMN last_error_code TEXT"
          );
        }
        this.#db.exec(
          "CREATE INDEX IF NOT EXISTS idx_agent_event_outbox_pending " +
          "ON agent_event_outbox(delivered_at, outbox_seq)"
        );
        this.#db.exec("COMMIT");
      } catch (error) {
        try { this.#db.exec("ROLLBACK"); } catch {}
        throw error;
      }
    } catch (error) {
      try { this.#db.close(); } catch {}
      this.#closed = true;
      throw error;
    }

    this.#insert = this.#db.prepare(
      "INSERT OR IGNORE INTO agent_event_outbox (event_id, event_json, inserted_at) VALUES (?, ?, ?)"
    );
    this.#select = this.#db.prepare(
      "SELECT event_json FROM agent_event_outbox WHERE event_id = ?"
    );
    this.#pending = this.#db.prepare(
      "SELECT event_json,next_attempt_at FROM agent_event_outbox " +
      "WHERE delivered_at IS NULL ORDER BY outbox_seq ASC LIMIT ?"
    );
    this.#ack = this.#db.prepare(
      "UPDATE agent_event_outbox SET delivered_at = ?, next_attempt_at = 0, last_error_code = NULL " +
      "WHERE event_id = ? AND delivered_at IS NULL"
    );
    this.#retry = this.#db.prepare(
      "UPDATE agent_event_outbox SET delivery_attempts = delivery_attempts + 1, " +
      "next_attempt_at = ?, last_error_code = ? WHERE event_id = ? AND delivered_at IS NULL"
    );
    this.#deliveryState = this.#db.prepare(
      "SELECT delivery_attempts,next_attempt_at,last_error_code FROM agent_event_outbox WHERE event_id = ?"
    );
    this.#counts = this.#db.prepare(
      "SELECT COUNT(*) AS total, " +
      "SUM(CASE WHEN delivered_at IS NULL THEN 1 ELSE 0 END) AS pending " +
      "FROM agent_event_outbox"
    );
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

  async listPending({limit = 100, now = Date.now()} = {}) {
    this.#assertOpen();
    const normalizedLimit = normalizeLimit(limit);
    const currentTime = normalizeTimestamp(now, "delivery_time");
    const rows = this.#pending.all(normalizedLimit);
    const events = [];
    for (const row of rows) {
      if (Number(row.next_attempt_at) > currentTime) break;
      events.push(JSON.parse(row.event_json));
    }
    return events;
  }

  async getDeliveryState(eventId) {
    this.#assertOpen();
    const id = requiredText(eventId, "event_id");
    const row = this.#deliveryState.get(id);
    if (!row) return null;
    return {
      attemptCount: Number(row.delivery_attempts),
      nextAttemptAt: Number(row.next_attempt_at),
      lastErrorCode: row.last_error_code ?? null,
    };
  }

  async scheduleRetry(eventId, {now = Date.now(), delayMs, errorCode} = {}) {
    this.#assertOpen();
    const id = requiredText(eventId, "event_id");
    const currentTime = normalizeTimestamp(now, "delivery_time");
    if (!Number.isFinite(delayMs) || delayMs < 0 || delayMs > MAX_RETRY_DELAY_MS) {
      throw new TypeError("invalid_delivery_retry_delay");
    }
    const code = normalizeErrorCode(errorCode);
    const nextAttemptAt = normalizeTimestamp(currentTime + delayMs, "delivery_retry_time");

    this.#db.exec("BEGIN IMMEDIATE");
    try {
      const result = this.#retry.run(nextAttemptAt, code, id);
      const row = this.#deliveryState.get(id);
      this.#db.exec("COMMIT");
      return {
        updated: Number(result.changes),
        attemptCount: row ? Number(row.delivery_attempts) : 0,
        nextAttemptAt: row ? Number(row.next_attempt_at) : null,
      };
    } catch (error) {
      try { this.#db.exec("ROLLBACK"); } catch {}
      throw error;
    }
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
