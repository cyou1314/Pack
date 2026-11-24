import test from "node:test";
import assert from "node:assert/strict";
import {AgentAdapterRegistry} from "../agent-core/registry.mjs";
import {RuntimeSupervisor} from "../agent-core/supervisor.mjs";
import {createAgentEvent} from "../agent-core/contract.mjs";

const descriptor = {
  adapterId: "test.adapter",
  providerId: "test",
  displayName: "Test Adapter",
  version: "1.0.0",
  mode: "local_runtime",
  authKind: "local_process",
  capabilities: [
    "message.send",
    "session.create",
    "session.resume",
    "session.interrupt",
    "events.push",
    "approvals.request",
  ],
};

class FakeAdapter {
  constructor(agentId, {connectGate, disconnectGate, disconnectFailures = 0, connectError, sendGate, onCreate} = {}) {
    this.agentId = agentId;
    this.connected = false;
    this.sequence = 0;
    this.listeners = new Set();
    this.responses = [];
    this.disconnectCalls = 0;
    this.disconnectFailuresRemaining = disconnectFailures;
    this.connectGate = connectGate;
    this.disconnectGate = disconnectGate;
    this.connectError = connectError;
    this.sendGate = sendGate;
    this.sendCalls = 0;
    onCreate?.(this);
  }

  subscribe(handler) {
    this.listeners.add(handler);
    return () => this.listeners.delete(handler);
  }

  emit(eventType, payload = {}, overrides = {}) {
    const event = createAgentEvent({
      eventId: "evt-" + this.agentId + "-" + (++this.sequence),
      eventType,
      providerId: overrides.providerId ?? "test",
      agentId: overrides.agentId ?? this.agentId,
      occurredAt: "2026-11-24T03:00:00.000Z",
      sequence: this.sequence,
      payload,
      ...(overrides.sessionId ? {sessionId: overrides.sessionId} : {}),
      ...(overrides.runId ? {runId: overrides.runId} : {}),
    });
    for (const listener of this.listeners) listener(event);
    return event;
  }

  async connect() {
    this.connected = true;
    this.emit("connection.changed", {status: "ready"});
    if (this.connectGate) await this.connectGate;
    if (this.connectError) throw new Error(this.connectError);
    return this.getHealth();
  }

  async disconnect() {
    this.disconnectCalls += 1;
    if (this.disconnectGate) await this.disconnectGate;
    if (this.disconnectFailuresRemaining > 0) {
      this.disconnectFailuresRemaining -= 1;
      throw new Error("provider_stop_failed");
    }
    this.connected = false;
    this.emit("connection.changed", {status: "disconnected"});
  }

  getHealth() {
    return {status: this.connected ? "ready" : "disconnected"};
  }

  async createSession({sessionId = "session-1"} = {}) {
    this.emit("session.created", {sessionId}, {sessionId});
    return {sessionId};
  }

  async resumeSession({sessionId = "session-1"} = {}) {
    return {sessionId};
  }

  async sendMessage({sessionId, text}) {
    this.sendCalls += 1;
    if (this.sendGate) await this.sendGate;
    this.emit("run.started", {text}, {sessionId, runId: "run-1"});
    return {accepted: true, runId: "run-1"};
  }

  async interruptSession({sessionId, runId}) {
    this.emit("run.interrupted", {runId}, {sessionId, runId});
    return {interrupted: true};
  }

  async respondToApproval({requestId, decision}) {
    this.responses.push({requestId, decision});
    return {accepted: true};
  }

  requestApproval(requestId = "approval-1") {
    return this.emit("approval.requested", {
      requestId,
      approvalKind: "command_execution",
      reason: "Run the requested command",
    });
  }
}

class FakeEventStore {
  constructor() {
    this.events = [];
    this.ids = new Set();
    this.failNext = false;
    this.appendGate = null;
  }

  async append(event) {
    if (this.appendGate) await this.appendGate;
    if (this.failNext) {
      this.failNext = false;
      throw new Error("disk_full");
    }
    if (this.ids.has(event.eventId)) return {inserted: false};
    this.ids.add(event.eventId);
    this.events.push(event);
    return {inserted: true};
  }
}

function createHarness({authorizeApproval, connectGate, disconnectGate, disconnectFailures, connectError, sendGate, onCreate} = {}) {
  const registry = new AgentAdapterRegistry();
  const adapters = [];
  registry.register({
    descriptor,
    create: async ({agentId}) => {
      const adapter = new FakeAdapter(agentId, {
        connectGate,
        disconnectGate,
        disconnectFailures,
        connectError,
        sendGate,
        onCreate: (instance) => {
          adapters.push(instance);
          onCreate?.(instance);
        },
      });
      return adapter;
    },
  });
  const eventStore = new FakeEventStore();
  const supervisor = new RuntimeSupervisor({registry, eventStore, authorizeApproval});
  return {supervisor, eventStore, adapters};
}

test("Supervisor persists adapter events before publishing them", async () => {
  const {supervisor, eventStore} = createHarness();
  const seen = [];
  supervisor.subscribe((event) => {
    assert.ok(eventStore.events.some((stored) => stored.eventId === event.eventId));
    seen.push(event);
  });

  const status = await supervisor.connectAgent({
    agentId: "agent-1",
    adapterId: "test.adapter",
  });

  assert.equal(status.status, "ready");
  assert.equal(seen.length, 1);
  assert.equal(seen[0].eventType, "connection.changed");
  assert.equal(status.pendingEventCount, 0);
});

test("Supervisor freezes nested event payloads before publishing", async () => {
  const {supervisor, adapters, eventStore} = createHarness();
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});
  supervisor.subscribe((event) => {
    if (event.eventType === "run.started") event.payload.nested.value = "mutated";
  });

  const emitted = adapters[0].emit("run.started", {nested: {value: "original"}});
  await supervisor.retryEvents("agent-1");
  const stored = eventStore.events.find((event) => event.eventId === emitted.eventId);

  assert.equal(stored.payload.nested.value, "original");
  assert.equal(emitted.payload.nested.value, "original");
  assert.equal(Object.isFrozen(emitted.payload.nested), true);
});

test("Supervisor owns session lifecycle and rejects unmanaged sessions", async () => {
  const {supervisor} = createHarness();
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});

  await assert.rejects(
    () => supervisor.sendMessage({agentId: "agent-1", sessionId: "unknown", text: "hello"}),
    /session_not_managed/,
  );

  const created = await supervisor.createSession("agent-1");
  assert.equal(created.sessionId, "session-1");
  const resumed = await supervisor.resumeSession("agent-1", {sessionId: "session-1"});
  assert.equal(resumed.sessionId, "session-1");

  const sent = await supervisor.sendMessage({
    agentId: "agent-1",
    sessionId: "session-1",
    text: "Inspect the change",
  });
  assert.equal(sent.runId, "run-1");

  const interrupted = await supervisor.interruptSession({
    agentId: "agent-1",
    sessionId: "session-1",
    runId: "run-1",
  });
  assert.equal(interrupted.interrupted, true);
});

test("Supervisor fails closed when no approval service is injected", async () => {
  const {supervisor, adapters} = createHarness();
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});
  adapters[0].requestApproval();
  await supervisor.retryEvents("agent-1");

  assert.equal(supervisor.getAgentStatus("agent-1").pendingApprovalCount, 1);
  await assert.rejects(
    () => supervisor.respondToApproval({
      agentId: "agent-1",
      requestId: "approval-1",
      decision: "accept",
    }),
    /approval_service_unavailable/,
  );
  assert.deepEqual(adapters[0].responses, []);
});

test("Supervisor requires an explicit approval authorization result", async () => {
  const {supervisor, adapters} = createHarness({
    authorizeApproval: async ({decision}) => decision === "decline",
  });
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});
  adapters[0].requestApproval();
  await supervisor.retryEvents("agent-1");

  await assert.rejects(
    () => supervisor.respondToApproval({
      agentId: "agent-1",
      requestId: "approval-1",
      decision: "accept",
    }),
    /approval_not_authorized/,
  );
  assert.deepEqual(adapters[0].responses, []);

  const result = await supervisor.respondToApproval({
    agentId: "agent-1",
    requestId: "approval-1",
    decision: "decline",
  });
  assert.equal(result.accepted, true);
  assert.deepEqual(adapters[0].responses, [{requestId: "approval-1", decision: "decline"}]);
  assert.equal(supervisor.getAgentStatus("agent-1").pendingApprovalCount, 0);
});

test("Supervisor fails closed when the approval service errors", async () => {
  const {supervisor, adapters} = createHarness({
    authorizeApproval: async () => { throw new Error("private approval backend detail"); },
  });
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});
  adapters[0].requestApproval();
  await supervisor.retryEvents("agent-1");

  await assert.rejects(
    () => supervisor.respondToApproval({
      agentId: "agent-1",
      requestId: "approval-1",
      decision: "accept",
    }),
    /approval_authorization_failed/,
  );
  assert.deepEqual(adapters[0].responses, []);
});

test("Connection failure remains failed when startup event persistence also fails", async () => {
  const {supervisor, eventStore, adapters} = createHarness({
    connectError: "provider_connect_failed",
  });
  eventStore.failNext = true;

  await assert.rejects(
    () => supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"}),
    /provider_connect_failed|event_persistence_failed/,
  );
  assert.equal(adapters[0].connected, false);
  assert.equal(supervisor.getAgentStatus("agent-1").status, "failed");

  const afterRetry = await supervisor.retryEvents("agent-1");
  assert.equal(afterRetry.status, "failed");
});

test("Event recovery cannot mark a still-pending connection ready", async () => {
  let releaseConnect;
  const connectGate = new Promise((resolve) => { releaseConnect = resolve; });
  const {supervisor, eventStore} = createHarness({connectGate});
  eventStore.failNext = true;

  const connecting = supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});
  await new Promise((resolve) => {
    const check = () => {
      if (supervisor.getAgentStatus("agent-1")?.status === "degraded") resolve();
      else setImmediate(check);
    };
    check();
  });

  const recoveredEvents = await supervisor.retryEvents("agent-1");
  assert.equal(recoveredEvents.status, "connecting");
  assert.equal(recoveredEvents.connectionEstablished, false);
  await assert.rejects(
    () => supervisor.sendMessage({agentId: "agent-1", sessionId: "session-1", text: "blocked"}),
    /agent_not_ready/,
  );

  releaseConnect();
  const ready = await connecting;
  assert.equal(ready.status, "ready");
  assert.equal(ready.connectionEstablished, true);
});

test("Supervisor revalidates readiness after asynchronous approval authorization", async () => {
  let beginAuthorization;
  let releaseAuthorization;
  const authorizationStarted = new Promise((resolve) => { beginAuthorization = resolve; });
  const authorizationGate = new Promise((resolve) => { releaseAuthorization = resolve; });
  const {supervisor, adapters, eventStore} = createHarness({
    authorizeApproval: async () => {
      beginAuthorization();
      await authorizationGate;
      return true;
    },
  });
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});
  adapters[0].requestApproval();
  await supervisor.retryEvents("agent-1");

  const response = supervisor.respondToApproval({
    agentId: "agent-1",
    requestId: "approval-1",
    decision: "accept",
  });
  await authorizationStarted;

  eventStore.failNext = true;
  adapters[0].emit("run.started", {text: "event write failure"});
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(supervisor.getAgentStatus("agent-1").status, "degraded");

  releaseAuthorization();
  await assert.rejects(response, /agent_not_ready/);
  assert.deepEqual(adapters[0].responses, []);
});

test("Event persistence failure degrades the agent and can be retried", async () => {
  const {supervisor, eventStore} = createHarness();
  eventStore.failNext = true;

  await assert.rejects(
    () => supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"}),
    /event_persistence_failed/,
  );
  assert.equal(supervisor.getAgentStatus("agent-1").status, "degraded");
  assert.equal(supervisor.getAgentStatus("agent-1").pendingEventCount, 1);

  await assert.rejects(
    () => supervisor.sendMessage({agentId: "agent-1", sessionId: "session-1", text: "blocked"}),
    /agent_not_ready/,
  );

  const recovered = await supervisor.retryEvents("agent-1");
  assert.equal(recovered.status, "ready");
  assert.equal(recovered.pendingEventCount, 0);
  assert.equal(eventStore.events.length, 1);
});

test("Supervisor rejects events whose identity does not match the managed adapter", async () => {
  const {supervisor, adapters} = createHarness();
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});

  adapters[0].emit("run.started", {text: "not trusted"}, {agentId: "agent-2"});
  await assert.rejects(() => supervisor.retryEvents("agent-1"), /agent_event_invalid/);
  assert.equal(supervisor.getAgentStatus("agent-1").status, "degraded");
});

test("Supervisor drains the final connection event before disconnect completes", async () => {
  const {supervisor, eventStore} = createHarness();
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});

  const status = await supervisor.disconnectAgent("agent-1");
  assert.equal(status.status, "disconnected");
  assert.ok(eventStore.events.some((event) =>
    event.eventType === "connection.changed" && event.payload.status === "disconnected"
  ));
});

test("Disconnect waits for in-flight commands before stopping the adapter", async () => {
  let releaseSend;
  const sendGate = new Promise((resolve) => { releaseSend = resolve; });
  const {supervisor, adapters} = createHarness({sendGate});
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});
  await supervisor.createSession("agent-1");

  const sending = supervisor.sendMessage({
    agentId: "agent-1",
    sessionId: "session-1",
    text: "finish before shutdown",
  });
  await new Promise((resolve) => {
    const check = () => {
      if (adapters[0].sendCalls === 1) resolve();
      else setImmediate(check);
    };
    check();
  });

  const disconnecting = supervisor.disconnectAgent("agent-1");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(adapters[0].disconnectCalls, 0);

  releaseSend();
  const sent = await sending;
  const disconnected = await disconnecting;
  assert.equal(sent.accepted, true);
  assert.equal(disconnected.status, "disconnected");
  assert.equal(adapters[0].disconnectCalls, 1);
});

test("Concurrent disconnect calls share one lifecycle operation", async () => {
  let releaseDisconnect;
  const disconnectGate = new Promise((resolve) => { releaseDisconnect = resolve; });
  const {supervisor, adapters} = createHarness({disconnectGate});
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});

  const first = supervisor.disconnectAgent("agent-1");
  await new Promise((resolve) => {
    const check = () => {
      if (adapters[0].disconnectCalls === 1) resolve();
      else setImmediate(check);
    };
    check();
  });
  const second = supervisor.disconnectAgent("agent-1");
  await assert.rejects(
    () => supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"}),
    /agent_lifecycle_busy/,
  );

  releaseDisconnect();
  const [firstStatus, secondStatus] = await Promise.all([first, second]);
  assert.equal(firstStatus.status, "disconnected");
  assert.equal(secondStatus.status, "disconnected");
  assert.equal(adapters[0].disconnectCalls, 1);
});

test("Disconnect failure retains the adapter for a later cleanup retry", async () => {
  const {supervisor, adapters} = createHarness({disconnectFailures: 1});
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});

  await assert.rejects(() => supervisor.disconnectAgent("agent-1"), /provider_stop_failed/);
  const failed = supervisor.getAgentStatus("agent-1");
  assert.equal(failed.status, "failed");
  assert.equal(failed.disconnectFailed, true);
  assert.equal(adapters[0].connected, true);
  assert.equal(adapters[0].listeners.size, 1);

  await assert.rejects(
    () => supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"}),
    /agent_disconnect_failed/,
  );

  const disconnected = await supervisor.disconnectAgent("agent-1");
  assert.equal(disconnected.status, "disconnected");
  assert.equal(adapters[0].disconnectCalls, 2);
  assert.equal(adapters[0].connected, false);
  assert.equal(adapters[0].listeners.size, 0);
});

test("Unexpected adapter disconnect updates state and permits a clean reconnect", async () => {
  const {supervisor, adapters} = createHarness();
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});

  adapters[0].connected = false;
  adapters[0].emit("connection.changed", {status: "disconnected"});
  await supervisor.retryEvents("agent-1");
  assert.equal(supervisor.getAgentStatus("agent-1").status, "disconnected");

  const reconnected = await supervisor.connectAgent({
    agentId: "agent-1",
    adapterId: "test.adapter",
  });
  assert.equal(reconnected.status, "ready");
  assert.equal(adapters.length, 2);
  assert.equal(adapters[0].listeners.size, 0);
});

test("Shutdown accounts for reconnect cleanup already in progress", async () => {
  let releaseAppend;
  const appendGate = new Promise((resolve) => { releaseAppend = resolve; });
  const {supervisor, adapters, eventStore} = createHarness();
  await supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});

  eventStore.appendGate = appendGate;
  adapters[0].connected = false;
  adapters[0].emit("connection.changed", {status: "disconnected"});

  const reconnecting = supervisor.connectAgent({
    agentId: "agent-1",
    adapterId: "test.adapter",
  });
  const shuttingDown = supervisor.shutdown();
  releaseAppend();

  await assert.rejects(reconnecting, /agent_lifecycle_busy/);
  const states = await shuttingDown;
  assert.equal(adapters.length, 1);
  assert.equal(states[0].status, "disconnected");
  assert.equal(adapters[0].disconnectCalls, 1);
});

test("Concurrent connect calls share one adapter instance", async () => {
  let releaseConnect;
  const connectGate = new Promise((resolve) => { releaseConnect = resolve; });
  const {supervisor, adapters} = createHarness({connectGate});
  const firstConnect = supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});
  await new Promise((resolve) => {
    const check = () => {
      if (adapters.length) resolve();
      else setImmediate(check);
    };
    check();
  });
  const secondConnect = supervisor.connectAgent({agentId: "agent-1", adapterId: "test.adapter"});
  releaseConnect();

  const [first, secondResult] = await Promise.all([firstConnect, secondConnect]);
  assert.equal(adapters.length, 1);
  assert.equal(first.status, "ready");
  assert.equal(secondResult.status, "ready");
});
