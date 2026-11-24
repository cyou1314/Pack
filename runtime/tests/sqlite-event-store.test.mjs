import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
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

test("SQLite outbox validates limits, IDs, and closed state", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await assert.rejects(() => store.listPending({limit: 0}), /invalid_event_limit/);
    await assert.rejects(() => store.markDelivered("event-1"), /invalid_event_ids/);
    store.close();
    await assert.rejects(() => store.listPending(), /event_store_closed/);
    store.close();
  });
});
