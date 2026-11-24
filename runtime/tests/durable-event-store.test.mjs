import test from "node:test";
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {createHash} from "node:crypto";
import {
  mkdtemp,
  mkdir,
  open,
  readFile,
  readdir,
  rm,
  stat,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {createAgentEvent} from "../agent-core/contract.mjs";
import {AgentAdapterRegistry} from "../agent-core/registry.mjs";
import {RuntimeSupervisor} from "../agent-core/supervisor.mjs";
import {DurableEventStore} from "../agent-core/durable-event-store.mjs";

const moduleUrl = new URL("../agent-core/durable-event-store.mjs", import.meta.url).href;

function makeEvent(eventId, payload = {text: "hello"}, sequence = 1) {
  return createAgentEvent({
    eventId,
    eventType: "message.completed",
    providerId: "test",
    agentId: "agent-1",
    occurredAt: "2026-11-24T03:00:00.000Z",
    sequence,
    payload,
  });
}

async function temporaryDirectory(t) {
  const parent = await mkdtemp(path.join(os.tmpdir(), "pack-event-store-"));
  t.after(async () => rm(parent, {recursive: true, force: true}));
  return path.join(parent, "store");
}

test("DurableEventStore persists ordered pending events across reopen", async (t) => {
  const directory = await temporaryDirectory(t);
  const first = await DurableEventStore.open({directory});
  assert.deepEqual(await first.append(makeEvent("event-1", {text: "one"}, 1)), {
    inserted: true,
    order: 1,
  });
  assert.deepEqual(await first.append(makeEvent("event-2", {text: "two"}, 2)), {
    inserted: true,
    order: 2,
  });
  assert.deepEqual((await first.listPending()).map((item) => item.event.eventId), [
    "event-1",
    "event-2",
  ]);
  await first.close();

  const reopened = await DurableEventStore.open({directory});
  const pending = await reopened.listPending();
  assert.deepEqual(pending.map((item) => item.order), [1, 2]);
  assert.deepEqual(pending.map((item) => item.event.payload.text), ["one", "two"]);
  await reopened.close();
});

test("DurableEventStore can back RuntimeSupervisor event persistence", async (t) => {
  const directory = await temporaryDirectory(t);
  const descriptor = {
    adapterId: "store.integration",
    providerId: "store-test",
    displayName: "Store Integration Adapter",
    version: "1.0.0",
    mode: "local_runtime",
    authKind: "local_process",
    capabilities: ["events.push"],
  };
  class StoreIntegrationAdapter {
    constructor(agentId) {
      this.agentId = agentId;
      this.connected = false;
      this.sequence = 0;
      this.listeners = new Set();
    }
    subscribe(handler) {
      this.listeners.add(handler);
      return () => this.listeners.delete(handler);
    }
    emit(status) {
      const event = createAgentEvent({
        eventId: "store-integration-" + (++this.sequence),
        eventType: "connection.changed",
        providerId: "store-test",
        agentId: this.agentId,
        occurredAt: "2026-11-24T03:00:00.000Z",
        sequence: this.sequence,
        payload: {status},
      });
      for (const listener of this.listeners) listener(event);
    }
    async connect() {
      this.connected = true;
      this.emit("ready");
    }
    async disconnect() {
      this.connected = false;
      this.emit("disconnected");
    }
    async sendMessage() { return {accepted: true}; }
    getHealth() { return {status: this.connected ? "ready" : "disconnected"}; }
  }

  const registry = new AgentAdapterRegistry();
  registry.register({
    descriptor,
    create: async ({agentId}) => new StoreIntegrationAdapter(agentId),
  });
  const store = await DurableEventStore.open({directory});
  const supervisor = new RuntimeSupervisor({registry, eventStore: store});
  const connected = await supervisor.connectAgent({
    agentId: "store-agent",
    adapterId: descriptor.adapterId,
  });
  assert.equal(connected.status, "ready");
  await supervisor.shutdown();
  await store.close();

  const reopened = await DurableEventStore.open({directory});
  const pending = await reopened.listPending();
  assert.deepEqual(pending.map((item) => item.event.payload.status), ["ready", "disconnected"]);
  await reopened.close();
});

test("Acknowledgements survive reopen and preserve eventId deduplication", async (t) => {
  const directory = await temporaryDirectory(t);
  const event = makeEvent("event-ack", {text: "deliver once"}, 7);
  const first = await DurableEventStore.open({directory});
  await first.append(event);
  assert.deepEqual(await first.acknowledge(event.eventId), {acknowledged: true});
  assert.deepEqual(await first.acknowledge(event.eventId), {acknowledged: false});
  assert.deepEqual(await first.listPending(), []);
  await first.close();

  const reopened = await DurableEventStore.open({directory});
  assert.deepEqual(await reopened.append(event), {inserted: false, order: 1});
  assert.deepEqual(await reopened.listPending(), []);
  await assert.rejects(
    () => reopened.append(makeEvent("event-ack", {text: "different content"}, 7)),
    /event_id_conflict/,
  );
  await reopened.close();
});

test("A failed acknowledgement directory sync leaves Pending intact and retries the sync", async (t) => {
  const directory = await temporaryDirectory(t);
  const store = await DurableEventStore.open({directory});
  const event = makeEvent("event-ack-sync-failure");
  await store.append(event);

  const handle = await open(directory, "r");
  const prototype = Object.getPrototypeOf(handle);
  await handle.close();
  const originalSync = prototype.sync;
  let syncCalls = 0;
  prototype.sync = async function(...args) {
    syncCalls += 1;
    if (syncCalls === 2) throw new Error("injected_sync_failure");
    return originalSync.apply(this, args);
  };

  try {
    await assert.rejects(() => store.acknowledge(event.eventId), /injected_sync_failure/);
  } finally {
    prototype.sync = originalSync;
  }

  assert.equal((await readdir(path.join(directory, "pending"))).length, 1);
  assert.deepEqual(await store.append(event), {inserted: false, order: 1});
  assert.equal((await readdir(path.join(directory, "pending"))).length, 1);
  assert.deepEqual(await store.acknowledge(event.eventId), {acknowledged: false});
  assert.deepEqual(await store.listPending(), []);
  assert.deepEqual(await store.append(event), {inserted: false, order: 1});
  await store.close();
});

test("A failed Pending directory sync is retried before duplicate append succeeds", async (t) => {
  const directory = await temporaryDirectory(t);
  const store = await DurableEventStore.open({directory});
  const event = makeEvent("event-pending-sync-failure");

  const handle = await open(directory, "r");
  const prototype = Object.getPrototypeOf(handle);
  await handle.close();
  const originalSync = prototype.sync;
  let syncCalls = 0;
  prototype.sync = async function(...args) {
    syncCalls += 1;
    if (syncCalls === 2) throw new Error("injected_pending_sync_failure");
    return originalSync.apply(this, args);
  };

  try {
    await assert.rejects(() => store.append(event), /injected_pending_sync_failure/);
  } finally {
    prototype.sync = originalSync;
  }

  assert.equal((await readdir(path.join(directory, "pending"))).length, 1);
  assert.equal((await store.listPending()).length, 1);
  assert.deepEqual(await store.append(event), {inserted: false, order: 1});
  assert.equal((await store.listPending()).length, 1);
  await store.close();
});

test("A failed record serialization removes its temp file and permits retry", async (t) => {
  const directory = await temporaryDirectory(t);
  const store = await DurableEventStore.open({directory});
  const event = makeEvent("event-temp-cleanup");
  const originalStringify = JSON.stringify;
  JSON.stringify = function(value, ...args) {
    if (value?.kind === "event") throw new Error("injected_record_serialization_failure");
    return originalStringify.call(this, value, ...args);
  };

  try {
    await assert.rejects(
      () => store.append(event),
      /injected_record_serialization_failure/,
    );
  } finally {
    JSON.stringify = originalStringify;
  }

  assert.deepEqual(await readdir(path.join(directory, "pending")), []);
  assert.deepEqual(await store.append(event), {inserted: true, order: 1});
  assert.equal((await store.listPending()).length, 1);
  await store.close();
});

test("Startup treats a durable acknowledgement as authoritative after an interrupted cleanup", async (t) => {
  const directory = await temporaryDirectory(t);
  const event = makeEvent("event-ack-crash", {text: "already delivered"}, 5);
  const store = await DurableEventStore.open({directory});
  await store.append(event);
  await store.close();

  const hash = createHash("sha256").update(event.eventId).digest("hex");
  const pendingPath = path.join(directory, "pending", hash + ".json");
  const acknowledgedPath = path.join(directory, "acknowledged", hash + ".json");
  const pendingRecord = JSON.parse(await readFile(pendingPath, "utf8"));
  await writeFile(acknowledgedPath, JSON.stringify({
    version: 1,
    kind: "ack",
    eventId: event.eventId,
    eventHash: pendingRecord.eventHash,
    order: pendingRecord.order,
    acknowledgedAt: "2026-11-24T03:05:00.000Z",
  }) + "\n", {mode: 0o600});

  const recovered = await DurableEventStore.open({directory});
  assert.deepEqual(await recovered.listPending(), []);
  assert.deepEqual(await readdir(path.join(directory, "pending")), []);
  assert.deepEqual(await recovered.append(event), {inserted: false, order: 1});
  await recovered.close();
});

test("Concurrent duplicate appends commit exactly one event", async (t) => {
  const directory = await temporaryDirectory(t);
  const store = await DurableEventStore.open({directory});
  const event = makeEvent("event-concurrent", {text: "same event"}, 3);
  const results = await Promise.all(
    Array.from({length: 20}, () => store.append(event)),
  );
  assert.equal(results.filter((result) => result.inserted).length, 1);
  assert.equal((await store.listPending()).length, 1);
  await store.close();
});

test("A process restart recovers an event committed before process exit", async (t) => {
  const directory = await temporaryDirectory(t);
  const event = makeEvent("event-process-exit", {text: "committed"}, 4);
  const script = [
    `import {DurableEventStore} from ${JSON.stringify(moduleUrl)};`,
    `const store = await DurableEventStore.open({directory: ${JSON.stringify(directory)}});`,
    `await store.append(${JSON.stringify(event)});`,
    "process.exit(0);",
  ].join("\n");
  const child = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
    encoding: "utf8",
  });
  assert.equal(child.status, 0, child.stderr || child.error?.message);

  const reopened = await DurableEventStore.open({directory});
  const pending = await reopened.listPending();
  assert.equal(pending.length, 1);
  assert.equal(pending[0].event.eventId, event.eventId);
  await reopened.close();
});

test("The store rejects non-JSON payloads and oversized events", async (t) => {
  const directory = await temporaryDirectory(t);
  const store = await DurableEventStore.open({directory, maxEventBytes: 1024});

  await assert.rejects(
    () => store.append(makeEvent("event-undefined", {nested: {value: undefined}})),
    /event_not_json_serializable/,
  );
  const circular = {};
  circular.self = circular;
  await assert.rejects(
    () => store.append({
      eventId: "event-circular",
      eventType: "message.completed",
      providerId: "test",
      agentId: "agent-1",
      occurredAt: "2026-11-24T03:00:00.000Z",
      sequence: 2,
      payload: {nested: circular},
    }),
    /event_not_json_serializable/,
  );
  await assert.rejects(
    () => store.append(makeEvent("event-large", {text: "x".repeat(2048)})),
    /event_too_large/,
  );
  let deeplyNested = {value: "x"};
  for (let index = 0; index < 70; index += 1) deeplyNested = {child: deeplyNested};
  await assert.rejects(
    () => store.append(makeEvent("event-deep", {nested: deeplyNested})),
    /event_payload_too_deep/,
  );
  await store.close();
});

test("The store rejects non-enumerable serialization hooks before writing", async (t) => {
  const directory = await temporaryDirectory(t);
  const store = await DurableEventStore.open({directory});
  const nested = {visible: "safe"};
  Object.defineProperty(nested, "toJSON", {
    enumerable: false,
    value() { return {apiKey: "must-not-be-persisted"}; },
  });

  await assert.rejects(
    () => store.append(makeEvent("event-to-json-hook", {nested})),
    /event_not_json_serializable/,
  );
  assert.deepEqual(await store.listPending(), []);
  await store.close();
});

test("The store uses private directory and event-file permissions", async (t) => {
  const directory = await temporaryDirectory(t);
  const store = await DurableEventStore.open({directory});
  await store.append(makeEvent("event-permissions"));
  const rootStat = await stat(directory);
  const pendingStat = await stat(path.join(directory, "pending"));
  const files = await readdir(path.join(directory, "pending"));
  const eventStat = await stat(path.join(directory, "pending", files[0]));

  assert.equal(rootStat.mode & 0o777, 0o700);
  assert.equal(pendingStat.mode & 0o777, 0o700);
  assert.equal(eventStat.mode & 0o777, 0o600);
  await store.close();
});

test("Startup removes abandoned temporary files but rejects corrupt committed records", async (t) => {
  const directory = await temporaryDirectory(t);
  const pendingDirectory = path.join(directory, "pending");
  const store = await DurableEventStore.open({directory});
  await store.close();

  await writeFile(path.join(pendingDirectory, ".tmp-abandoned"), "partial", {mode: 0o600});
  const reopened = await DurableEventStore.open({directory});
  assert.deepEqual(await readdir(pendingDirectory), []);
  await reopened.close();

  await writeFile(path.join(pendingDirectory, "invalid.json"), "{}\n", {mode: 0o600});
  await assert.rejects(
    () => DurableEventStore.open({directory}),
    /event_store_corrupt/,
  );
});

test("The store requires its parent hierarchy to exist before creating the data directory", async (t) => {
  const directory = await temporaryDirectory(t);
  const nestedDirectory = path.join(directory, "missing-parent", "store");
  await assert.rejects(
    () => DurableEventStore.open({directory: nestedDirectory}),
    /event_store_parent_missing/,
  );
});

test("A retry re-syncs an existing marker after its first file sync fails", async (t) => {
  const directory = await temporaryDirectory(t);
  const parent = path.dirname(directory);
  const handle = await open(parent, "r");
  const prototype = Object.getPrototypeOf(handle);
  await handle.close();
  const originalSync = prototype.sync;
  let syncCalls = 0;
  prototype.sync = async function(...args) {
    syncCalls += 1;
    if (syncCalls === 1) throw new Error("injected_marker_sync_failure");
    return originalSync.apply(this, args);
  };

  try {
    await assert.rejects(
      () => DurableEventStore.open({directory}),
      /injected_marker_sync_failure/,
    );
  } finally {
    prototype.sync = originalSync;
  }

  const recovered = await DurableEventStore.open({directory});
  await recovered.append(makeEvent("event-after-marker-retry"));
  await recovered.close();

  const reopened = await DurableEventStore.open({directory});
  assert.equal((await reopened.listPending()).length, 1);
  await reopened.close();
});

test("Event ID hashing preserves distinct unpaired UTF-16 surrogates", async (t) => {
  const directory = await temporaryDirectory(t);
  const store = await DurableEventStore.open({directory});
  const firstId = "event-\\uD800";
  const secondId = "event-\\uD801";
  await store.append(makeEvent(firstId, {text: "first identity"}));

  assert.deepEqual(await store.acknowledge(secondId), {acknowledged: false});
  assert.equal((await store.listPending()).length, 1);
  assert.deepEqual(await store.acknowledge(firstId), {acknowledged: true});
  assert.deepEqual(await store.listPending(), []);
  await store.close();
});

test("The store refuses a symlink as its data directory", async (t) => {
  const target = await temporaryDirectory(t);
  await mkdir(target, {recursive: true});
  const linkPath = path.join(os.tmpdir(), "pack-event-store-link-" + process.pid + "-" + Date.now());
  t.after(async () => rm(linkPath, {recursive: true, force: true}));
  await symlink(target, linkPath);
  await assert.rejects(
    () => DurableEventStore.open({directory: linkPath}),
    /event_store_unsafe_path/,
  );
});

test("Unknown acknowledgements are harmless and do not create tombstones", async (t) => {
  const directory = await temporaryDirectory(t);
  const store = await DurableEventStore.open({directory});
  assert.deepEqual(await store.acknowledge("missing-event"), {acknowledged: false});
  assert.deepEqual(await store.listPending(), []);
  await store.close();
});

test("Closed stores reject further reads and writes", async (t) => {
  const directory = await temporaryDirectory(t);
  const store = await DurableEventStore.open({directory});
  await store.close();
  await assert.rejects(() => store.append(makeEvent("event-after-close")), /event_store_closed/);
  await assert.rejects(() => store.listPending(), /event_store_closed/);
});
