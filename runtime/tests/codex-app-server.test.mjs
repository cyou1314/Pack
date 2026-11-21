import test from "node:test";
import assert from "node:assert/strict";
import {EventEmitter} from "node:events";
import {PassThrough} from "node:stream";
import {
  CodexAppServerAdapter,
  CODEX_APP_SERVER_DESCRIPTOR,
  registerCodexAppServerAdapter,
} from "../agent-core/adapters/codex-app-server.mjs";
import {AgentAdapterRegistry} from "../agent-core/registry.mjs";

class FakeCodexProcess extends EventEmitter {
  constructor({approvalOnTurn = false, approvalDecisions = ["accept", "decline", "cancel"]} = {}) {
    super();
    this.stdin = new PassThrough();
    this.stdout = new PassThrough();
    this.stderr = new PassThrough();
    this.exitCode = null;
    this.killed = false;
    this.approvalOnTurn = approvalOnTurn;
    this.approvalDecisions = approvalDecisions;
    this.received = [];
    this.buffer = "";

    this.stdin.on("data", (chunk) => {
      this.buffer += chunk.toString("utf8");
      for (;;) {
        const index = this.buffer.indexOf("\n");
        if (index < 0) break;
        const line = this.buffer.slice(0, index);
        this.buffer = this.buffer.slice(index + 1);
        if (!line.trim()) continue;
        const message = JSON.parse(line);
        this.received.push(message);
        this.#handle(message);
      }
    });
  }

  #send(message) {
    this.stdout.write(JSON.stringify(message) + "\n");
  }

  #handle(message) {
    if (message.method === "initialize") {
      this.#send({id: message.id, result: {userAgent: "fake-codex"}});
      return;
    }
    if (message.method === "thread/start") {
      this.#send({id: message.id, result: {thread: {id: "thr-new"}}});
      return;
    }
    if (message.method === "thread/resume") {
      this.#send({id: message.id, result: {thread: {id: message.params.threadId}}});
      return;
    }
    if (message.method === "turn/start") {
      const threadId = message.params.threadId;
      this.#send({id: message.id, result: {turn: {id: "turn-1", status: "inProgress"}}});
      this.#send({method: "turn/started", params: {threadId, turn: {id: "turn-1", status: "inProgress"}}});
      if (this.approvalOnTurn) {
        this.#send({
          id: 900,
          method: "item/commandExecution/requestApproval",
          params: {
            threadId,
            turnId: "turn-1",
            itemId: "item-approval",
            reason: "Run the requested test command",
            availableDecisions: this.approvalDecisions,
          },
        });
      } else {
        this.#send({
          method: "item/agentMessage/delta",
          params: {threadId, turnId: "turn-1", itemId: "item-1", delta: "Hello"},
        });
        this.#send({
          method: "turn/completed",
          params: {threadId, turn: {id: "turn-1", status: "completed"}},
        });
      }
      return;
    }
    if (message.method === "turn/interrupt") {
      this.#send({id: message.id, result: {}});
      return;
    }
    if (message.id === 900 && message.result === "accept") {
      this.#send({
        method: "turn/completed",
        params: {threadId: "thr-new", turn: {id: "turn-1", status: "completed"}},
      });
    }
  }

  kill(signal) {
    this.killed = true;
    this.exitCode = 0;
    this.emit("exit", 0, signal);
    return true;
  }
}

function waitForEvent(adapter, eventType, timeoutMs = 1000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      unsubscribe();
      reject(new Error("event_timeout:" + eventType));
    }, timeoutMs);
    const unsubscribe = adapter.subscribe((event) => {
      if (event.eventType !== eventType) return;
      clearTimeout(timer);
      unsubscribe();
      resolve(event);
    });
  });
}

test("Codex App Server initializes, creates/resumes sessions and streams turns", async () => {
  const child = new FakeCodexProcess();
  const adapter = new CodexAppServerAdapter({
    spawnProcess: () => child,
    requestTimeoutMs: 1000,
    agentId: "codex-test",
    roomId: "pack-main",
  });
  const events = [];
  adapter.subscribe((event) => events.push(event));

  await adapter.connect();
  const created = await adapter.createSession({cwd: "/tmp/project", model: "test-model"});
  assert.equal(created.sessionId, "thr-new");
  const resumed = await adapter.resumeSession({sessionId: "thr-new"});
  assert.equal(resumed.sessionId, "thr-new");

  const completed = waitForEvent(adapter, "run.completed");
  const sent = await adapter.sendMessage({sessionId: "thr-new", text: "Inspect this project"});
  assert.equal(sent.runId, "turn-1");
  await completed;

  assert.ok(child.received.some((item) => item.method === "initialize"));
  assert.ok(child.received.some((item) => item.method === "initialized"));
  assert.ok(child.received.some((item) => item.method === "thread/start"));
  assert.ok(child.received.some((item) => item.method === "thread/resume"));
  assert.ok(child.received.some((item) => item.method === "turn/start"));
  assert.ok(events.some((event) => event.eventType === "message.delta" && event.payload.delta === "Hello"));

  await adapter.disconnect();
  assert.equal(child.killed, true);
  assert.equal(adapter.getHealth().status, "disconnected");
});

test("Codex approval requests fail closed and require an explicit decision", async () => {
  const child = new FakeCodexProcess({approvalOnTurn: true});
  const adapter = new CodexAppServerAdapter({
    spawnProcess: () => child,
    requestTimeoutMs: 1000,
    approvalTimeoutMs: 5000,
  });
  await adapter.connect();
  await adapter.createSession();
  const approvalEvent = waitForEvent(adapter, "approval.requested");
  await adapter.sendMessage({sessionId: "thr-new", text: "Run tests"});
  const event = await approvalEvent;

  assert.equal(event.payload.approvalKind, "command_execution");
  assert.equal(event.payload.reason, "Run the requested test command");
  assert.equal(adapter.getHealth().pendingApprovals, 1);
  await assert.rejects(
    () => adapter.respondToApproval({requestId: event.payload.requestId, decision: "acceptForSession"}),
    /unsupported_codex_approval_decision/,
  );
  await adapter.respondToApproval({requestId: event.payload.requestId, decision: "accept"});
  assert.ok(child.received.some((item) => item.id === 900 && item.result === "accept"));
  await adapter.disconnect();
});

test("Codex adapter can be registered in the shared registry", async () => {
  const registry = new AgentAdapterRegistry();
  registerCodexAppServerAdapter(registry, {spawnProcess: () => new FakeCodexProcess()});
  assert.equal(registry.get("codex.app-server").providerId, "codex");
  assert.ok(CODEX_APP_SERVER_DESCRIPTOR.capabilities.includes("approvals.request"));
  const adapter = await registry.create("codex.app-server");
  assert.equal(typeof adapter.sendMessage, "function");
  await adapter.disconnect();
});

test("Codex approval never upgrades to a persistent session grant", async () => {
  const child = new FakeCodexProcess({approvalOnTurn: true, approvalDecisions: ["acceptForSession"]});
  const adapter = new CodexAppServerAdapter({
    spawnProcess: () => child,
    requestTimeoutMs: 1000,
    approvalTimeoutMs: 5000,
  });
  await adapter.connect();
  await adapter.createSession();
  const approvalEvent = waitForEvent(adapter, "approval.requested");
  await adapter.sendMessage({sessionId: "thr-new", text: "Run a command"});
  const event = await approvalEvent;
  await assert.rejects(
    () => adapter.respondToApproval({requestId: event.payload.requestId, decision: "accept"}),
    /codex_approval_decision_unavailable/,
  );
  await adapter.disconnect();
});
