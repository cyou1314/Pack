// Test-only real Node Outbox/Worker client. Credentials travel through stdin.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {SqliteEventStore} from "../../agent-core/sqlite-event-store.mjs";
import {AgentEventDeliveryWorker} from "../../agent-core/event-delivery-worker.mjs";

const {databasePath, endpoint, token, events, mode} = JSON.parse(readFileSync(0, "utf8"));
const store = new SqliteEventStore({databasePath});
let now = 1000;
const worker = new AgentEventDeliveryWorker({
  eventStore: store, endpoint, getToken: async () => token,
  clock: () => now, baseRetryMs: 1000, timeoutMs: 5000,
});
try {
  if (mode === "lost" || mode === "denied") {
    for (const event of events) await store.append(event);
    const result = await worker.runOnce();
    assert.equal(result.status, mode === "lost" ? "retry_scheduled" : "paused");
    if (mode === "denied") assert.equal(result.reason, "authorization_required");
    assert.deepEqual(await store.getStats(), {total: events.length, pending: events.length});
    console.log(JSON.stringify({status: result.status}));
  } else {
    if (mode === "retry") {
      assert.equal((await worker.runOnce()).status, "waiting");
      now = 2000;
    }
    const receipts = [];
    for (const event of events) {
      const result = await worker.runOnce();
      assert.equal(result.status, "delivered");
      assert.equal(result.eventId, event.eventId);
      receipts.push({inboxSeq: result.inboxSeq, duplicate: result.duplicate});
    }
    assert.equal((await worker.runOnce()).status, "idle");
    assert.deepEqual(await store.getStats(), {total: events.length, pending: 0});
    console.log(JSON.stringify({receipts}));
  }
} finally {
  store.close();
}
