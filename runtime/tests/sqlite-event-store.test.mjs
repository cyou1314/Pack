import test from "node:test";
import assert from "node:assert/strict";
import {DatabaseSync} from "node:sqlite";
import {chmodSync, mkdirSync, mkdtempSync, rmSync, statSync} from "node:fs";
import {tmpdir} from "node:os";
import {dirname, join} from "node:path";
import {SqliteEventStore} from "../agent-core/sqlite-event-store.mjs";

function makeEvent(eventId, sequence = 1) {
  return Object.freeze({
    eventId,
    eventType: "run.progress",
    providerId: "test",
    agentId: "agent-1",
    sequence,
    occurredAt: "2026-11-24T03:00:00.000Z",
    payload: {progress: sequence, nested: {stable: true}},
  });
}

async function withDatabase(run) {
  const directory = mkdtempSync(join(tmpdir(), "pack-event-store-"));
  const databasePath = join(directory, "runtime", "events.sqlite");
  try {
    return await run(databasePath);
  } finally {
    rmSync(directory, {recursive: true, force: true});
  }
}

test("SQLite database and WAL sidecars stay owner-only in an existing permissive directory", async () => {
  await withDatabase(async (databasePath) => {
    const directory = dirname(databasePath);
    mkdirSync(directory, {recursive: true, mode: 0o755});
    chmodSync(directory, 0o755);

    const store = new SqliteEventStore({databasePath});
    assert.equal(statSync(databasePath).mode & 0o777, 0o600);

    await store.append(makeEvent("private-event"));
    assert.equal(statSync(databasePath).mode & 0o777, 0o600);

    for (const sidecar of [databasePath + "-wal", databasePath + "-shm"]) {
      try {
        assert.equal(statSync(sidecar).mode & 0o777, 0o600, sidecar + " must be owner-only");
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
      }
    }
    store.close();
  });
});

test("SQLite outbox survives close and reopen with pending events intact", async () => {
  await withDatabase(async (databasePath) => {
    let store = new SqliteEventStore({databasePath});
    const first = makeEvent("event-1", 1);
    const second = makeEvent("event-2", 2);
    assert.deepEqual(await store.append(first), {inserted: true});
    assert.deepEqual(await store.append(second), {inserted: true});
    assert.deepEqual(await store.getStats(), {total: 2, pending: 2});
    store.close();

    store = new SqliteEventStore({databasePath});
    assert.deepEqual(await store.listPending(), [first, second]);
    assert.deepEqual(await store.getStats(), {total: 2, pending: 2});
    store.close();
  });
});

test("SQLite outbox deduplicates identical events and rejects event ID collisions", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    const event = makeEvent("event-1", 1);
    assert.deepEqual(await store.append(event), {inserted: true});
    assert.deepEqual(await store.append(event), {inserted: false});
    await assert.rejects(
      () => store.append({...event, payload: {progress: 99}}),
      /event_id_payload_conflict/,
    );
    assert.deepEqual(await store.getStats(), {total: 1, pending: 1});
    store.close();
  });
});

test("SQLite outbox acknowledges only explicitly delivered events", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    const first = makeEvent("event-1", 1);
    const second = makeEvent("event-2", 2);
    await store.append(first);
    await store.append(second);

    assert.deepEqual(await store.markDelivered(["event-1", "event-1", "unknown"]), {updated: 1});
    assert.deepEqual(await store.listPending(), [second]);
    assert.deepEqual(await store.getStats(), {total: 2, pending: 1});
    assert.deepEqual(await store.markDelivered(["event-1", "event-2"]), {updated: 1});
    assert.deepEqual(await store.listPending(), []);
    store.close();
  });
});

test("SQLite outbox retry metadata survives reopen and blocks later events until due", async () => {
  await withDatabase(async (databasePath) => {
    let store = new SqliteEventStore({databasePath});
    const first = makeEvent("event-1", 1);
    const second = makeEvent("event-2", 2);
    await store.append(first);
    await store.append(second);

    assert.deepEqual(
      await store.scheduleRetry("event-1", {
        now: 1000,
        delayMs: 5000,
        errorCode: "network_error",
      }),
      {updated: 1, attemptCount: 1, nextAttemptAt: 6000},
    );
    assert.deepEqual(
      await store.getDeliveryState("event-1"),
      {attemptCount: 1, nextAttemptAt: 6000, lastErrorCode: "network_error"},
    );
    assert.deepEqual(await store.listPending({now: 5999}), []);
    store.close();

    store = new SqliteEventStore({databasePath});
    assert.deepEqual(await store.listPending({now: 5999}), []);
    assert.deepEqual(await store.listPending({now: 6000}), [first, second]);
    await store.markDelivered(["event-1"]);
    assert.deepEqual(
      await store.getDeliveryState("event-1"),
      {attemptCount: 1, nextAttemptAt: 0, lastErrorCode: null},
    );
    store.close();
  });
});

test("SQLite outbox upgrades the previous schema without losing pending events", async () => {
  await withDatabase(async (databasePath) => {
    const event = makeEvent("legacy-event", 1);
    mkdirSync(dirname(databasePath), {recursive: true});
    const legacy = new DatabaseSync(databasePath);
    legacy.exec(
      "CREATE TABLE agent_event_outbox (" +
      "outbox_seq INTEGER PRIMARY KEY AUTOINCREMENT," +
      "event_id TEXT NOT NULL UNIQUE," +
      "event_json TEXT NOT NULL," +
      "inserted_at TEXT NOT NULL," +
      "delivered_at TEXT)"
    );
    legacy.prepare(
      "INSERT INTO agent_event_outbox(event_id,event_json,inserted_at) VALUES(?,?,?)"
    ).run(event.eventId, JSON.stringify(event), new Date().toISOString());
    legacy.close();

    const store = new SqliteEventStore({databasePath});
    assert.deepEqual(await store.listPending(), [event]);
    assert.deepEqual(
      await store.getDeliveryState("legacy-event"),
      {attemptCount: 0, nextAttemptAt: 0, lastErrorCode: null},
    );
    store.close();
  });
});

test("SQLite outbox validates limits, retry metadata, IDs, and closed state", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await assert.rejects(() => store.listPending({limit: 0}), /invalid_event_limit/);
    await assert.rejects(
      () => store.scheduleRetry("event-1", {now: 1, delayMs: -1, errorCode: "network_error"}),
      /invalid_delivery_retry_delay/,
    );
    await assert.rejects(
      () => store.scheduleRetry("event-1", {now: 1, delayMs: 1, errorCode: "unsafe error text"}),
      /invalid_delivery_error_code/,
    );
    await assert.rejects(() => store.markDelivered("event-1"), /invalid_event_ids/);
    store.close();
    await assert.rejects(() => store.listPending(), /event_store_closed/);
    store.close();
  });
});
