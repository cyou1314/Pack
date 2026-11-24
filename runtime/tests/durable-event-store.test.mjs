import test from "node:test";
import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {createHash} from "node:crypto";
import {
  mkdtemp,
  mkdir,
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
