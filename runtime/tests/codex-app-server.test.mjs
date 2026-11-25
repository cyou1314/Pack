import test from "node:test";
import assert from "node:assert/strict";
import {EventEmitter} from "node:events";
import {PassThrough} from "node:stream";
import {createHash} from "node:crypto";
import {
  CodexAppServerAdapter,
  CODEX_APP_SERVER_DESCRIPTOR,
  registerCodexAppServerAdapter,
} from "../agent-core/adapters/codex-app-server.mjs";
import {AgentAdapterRegistry} from "../agent-core/registry.mjs";

class FakeCodexProcess extends EventEmitter {
  constructor({
    approvalOnTurn = false,
    approvalOnThreadStart = false,
    resolveApprovalOnThreadStart = false,
    approvalThreadId = "thr-new",
    notificationOnThreadStart = false,
    threadStartDelayMs = 0,
    messageDeltaText = "Hello",
    messageCompletedText = "Hello",
    approvalDecisions = ["accept", "decline", "cancel"],
  } = {}) {
    super();
    this.stdin = new PassThrough();
    this.stdout = new PassThrough();
    this.stderr = new PassThrough();
    this.exitCode = null;
    this.killed = false;
    this.approvalOnTurn = approvalOnTurn;
    this.approvalOnThreadStart = approvalOnThreadStart;
    this.resolveApprovalOnThreadStart = resolveApprovalOnThreadStart;
    this.approvalThreadId = approvalThreadId;
    this.notificationOnThreadStart = notificationOnThreadStart;
    this.threadStartDelayMs = threadStartDelayMs;
    this.messageDeltaText = messageDeltaText;
    this.messageCompletedText = messageCompletedText;
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
      if (this.approvalOnThreadStart) {
        this.#send({
          id: 899,
          method: "item/commandExecution/requestApproval",
          params: {
            threadId: this.approvalThreadId,
            turnId: "turn-create",
            itemId: "item-create-approval",
            reason: "Approval during session creation",
            availableDecisions: this.approvalDecisions,
          },
        });
      }
      if (this.resolveApprovalOnThreadStart) {
        this.#send({
          method: "serverRequest/resolved",
          params: {
            requestId: "899",
            threadId: this.approvalThreadId,
          },
        });
      }
      if (this.notificationOnThreadStart) {
        this.#send({
          method: "turn/started",
          params: {threadId: "thr-new", turn: {id: "turn-before-start", status: "inProgress"}},
        });
      }
      const sendResult = () => this.#send({
        id: message.id,
        result: {thread: {id: "thr-new"}},
      });
      if (this.threadStartDelayMs > 0) {
        setTimeout(sendResult, this.threadStartDelayMs);
      } else {
        sendResult();
      }
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
          params: {
            threadId,
            turnId: "turn-1",
            itemId: "item-1",
            delta: this.messageDeltaText,
          },
        });
        this.#send({
          method: "item/completed",
          params: {
            threadId,
            turnId: "turn-1",
            item: {
              id: "item-1",
              type: "agentMessage",
              text: this.messageCompletedText,
              phase: "final_answer",
            },
          },
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
  const child = new FakeCodexProcess({notificationOnThreadStart: true});
  const adapter = new CodexAppServerAdapter({
    spawnProcess: () => child,
    requestTimeoutMs: 1000,
    agentId: "codex-test",
    roomId: "pack-main",
  });
  const events = [];
  adapter.subscribe((event) => events.push(event));

  await adapter.connect();
  const created = await adapter.createSession({
    cwd: "/tmp/project",
    model: "test-model",
    hubSessionId: "hub-session-1",
    hubSessionCapabilities: {hubSessionIdField: true},
  });
  assert.equal(created.sessionId, "thr-new");
  const resumed = await adapter.resumeSession({
    sessionId: "thr-new",
    hubSessionId: "hub-session-1",
    hubSessionCapabilities: {hubSessionIdField: true},
  });
  assert.equal(resumed.sessionId, "thr-new");

  const completed = waitForEvent(adapter, "run.completed");
  const sent = await adapter.sendMessage({sessionId: "thr-new", text: "Inspect this project"});
  assert.equal(sent.runId, "turn-1");
  await completed;

  assert.ok(child.received.some((item) => item.method === "initialize"));
  assert.ok(child.received.some((item) => item.method === "initialized"));
  const startRequest = child.received.find((item) => item.method === "thread/start");
  assert.ok(startRequest);
  assert.equal(Object.hasOwn(startRequest.params, "hubSessionId"), false);
  assert.ok(child.received.some((item) => item.method === "thread/resume"));
  assert.ok(child.received.some((item) => item.method === "turn/start"));
  const sessionCreatedIndex = events.findIndex(
    (event) => event.eventType === "session.created",
  );
  const earlyRunIndex = events.findIndex(
    (event) => event.eventType === "run.started" && event.runId === "turn-before-start",
  );
  assert.ok(sessionCreatedIndex >= 0);
  assert.ok(earlyRunIndex > sessionCreatedIndex);
  assert.equal(events[sessionCreatedIndex].hubSessionId, "hub-session-1");
  assert.equal(events[earlyRunIndex].hubSessionId, "hub-session-1");
  assert.ok(events.some((event) => event.eventType === "message.delta" && event.payload.delta === "Hello"));
  assert.ok(events.some((event) => event.eventType === "message.completed" && event.payload.text === "Hello"));
  assert.ok(events.some((event) => event.eventType === "run.completed" && event.hubSessionId === "hub-session-1"));
  await assert.rejects(
    () => adapter.resumeSession({
      sessionId: "thr-new",
      hubSessionId: "different-hub-session",
      hubSessionCapabilities: {hubSessionIdField: true},
    }),
    /hub_session_binding_conflict/,
  );

  await adapter.disconnect();
  assert.equal(child.killed, true);
  assert.equal(adapter.getHealth().status, "disconnected");
});

test("Codex preserves long message deltas and verifies oversized completion text losslessly", async () => {
  const longText = "A".repeat(3999) + "😀" + "B".repeat(30000);
  const child = new FakeCodexProcess({
    messageDeltaText: longText,
    messageCompletedText: longText,
  });
  const adapter = new CodexAppServerAdapter({
    spawnProcess: () => child,
    requestTimeoutMs: 1000,
  });
  const events = [];
  adapter.subscribe((event) => events.push(event));
  await adapter.connect();
  await adapter.createSession();
  await adapter.sendMessage({sessionId: "thr-new", text: "write a long response"});

  const deltas = events.filter((event) => event.eventType === "message.delta");
  assert.ok(deltas.length > 1);
  assert.equal(deltas.map((event) => event.payload.delta).join(""), longText);
  const completed = events.find((event) => event.eventType === "message.completed");
  assert.ok(completed);
  assert.equal(completed.payload.textAvailable, true);
  assert.equal(completed.payload.textIncluded, false);
  assert.equal(completed.payload.textByteLength, Buffer.byteLength(longText, "utf8"));
  assert.equal(
    completed.payload.textSha256,
    createHash("sha256").update(longText, "utf8").digest("hex"),
  );
  assert.equal(Object.hasOwn(completed.payload, "text"), false);
  await adapter.disconnect();
});

test("Codex session binding requires negotiated Hub capability", async () => {
  const child = new FakeCodexProcess();
  const adapter = new CodexAppServerAdapter({
    spawnProcess: () => child,
    requestTimeoutMs: 1000,
  });
  await adapter.connect();
  await assert.rejects(
    () => adapter.createSession({hubSessionId: "hub-session-1"}),
    /hub_session_binding_not_negotiated/,
  );
  assert.equal(child.received.some((item) => item.method === "thread/start"), false);
  await adapter.disconnect();
});

test("Codex processes early approval resolution before flushing the buffered request", async () => {
  const child = new FakeCodexProcess({
    approvalOnThreadStart: true,
    resolveApprovalOnThreadStart: true,
  });
  const adapter = new CodexAppServerAdapter({
    spawnProcess: () => child,
    requestTimeoutMs: 1000,
    approvalTimeoutMs: 5000,
  });
  const events = [];
  adapter.subscribe((event) => events.push(event));
  await adapter.connect();
  await adapter.createSession({
    hubSessionId: "hub-session-resolved",
    hubSessionCapabilities: {hubSessionIdField: true},
  });
  assert.equal(
    events.some((event) => event.eventType === "approval.requested"),
    false,
  );
  await adapter.disconnect();
});

test("Codex does not publish a buffered approval after its timeout", async () => {
  const child = new FakeCodexProcess({
    approvalOnThreadStart: true,
    threadStartDelayMs: 100,
  });
  const adapter = new CodexAppServerAdapter({
    spawnProcess: () => child,
    requestTimeoutMs: 1000,
    approvalTimeoutMs: 20,
  });
  const events = [];
  adapter.subscribe((event) => events.push(event));
  await adapter.connect();
  await adapter.createSession({
    hubSessionId: "hub-session-timeout",
    hubSessionCapabilities: {hubSessionIdField: true},
  });
  assert.equal(
    events.some((event) => event.eventType === "approval.requested"),
    false,
  );
  await adapter.disconnect();
});

test("Codex early approval from an unknown Session is never assigned the pending Hub ID", async () => {
  const child = new FakeCodexProcess({
    approvalOnThreadStart: true,
    approvalThreadId: "unknown-thread",
  });
  const adapter = new CodexAppServerAdapter({
    spawnProcess: () => child,
    requestTimeoutMs: 1000,
    approvalTimeoutMs: 5000,
  });
  const events = [];
  adapter.subscribe((event) => events.push(event));
  await adapter.connect();
  await adapter.createSession({
    hubSessionId: "hub-session-new",
    hubSessionCapabilities: {hubSessionIdField: true},
  });

  const approval = events.find((event) => event.eventType === "approval.requested");
  assert.ok(approval);
  assert.equal(approval.sessionId, "unknown-thread");
  assert.equal(approval.hubSessionId, undefined);
  await adapter.disconnect();
});

test("Codex early approval from a different Session keeps its existing Hub binding", async () => {
  const child = new FakeCodexProcess({
    approvalOnThreadStart: true,
    approvalThreadId: "legacy-thread",
  });
  const adapter = new CodexAppServerAdapter({
    spawnProcess: () => child,
    requestTimeoutMs: 1000,
    approvalTimeoutMs: 5000,
  });
  const events = [];
  adapter.subscribe((event) => events.push(event));
  await adapter.connect();
  await adapter.resumeSession({
    sessionId: "legacy-thread",
    hubSessionId: "hub-session-legacy",
    hubSessionCapabilities: {hubSessionIdField: true},
  });

  await adapter.createSession({
    hubSessionId: "hub-session-new",
    hubSessionCapabilities: {hubSessionIdField: true},
  });

  const approval = events.find((event) => event.eventType === "approval.requested");
  const session = events.find((event) => event.eventType === "session.created");
  assert.ok(approval);
  assert.equal(approval.sessionId, "legacy-thread");
  assert.equal(approval.hubSessionId, "hub-session-legacy");
  assert.ok(session);
  assert.equal(session.hubSessionId, "hub-session-new");
  await adapter.disconnect();
});

test("Codex session creation does not deadlock on an early approval request", async () => {
  const child = new FakeCodexProcess({approvalOnThreadStart: true});
  const adapter = new CodexAppServerAdapter({
    spawnProcess: () => child,
    requestTimeoutMs: 1000,
    approvalTimeoutMs: 5000,
  });
  const events = [];
  adapter.subscribe((event) => events.push(event));
  await adapter.connect();

  const created = await adapter.createSession({
    hubSessionId: "hub-session-approval",
    hubSessionCapabilities: {hubSessionIdField: true},
  });
  assert.equal(created.sessionId, "thr-new");
  const approval = events.find((event) => event.eventType === "approval.requested");
  const session = events.find((event) => event.eventType === "session.created");
  assert.ok(approval);
  assert.equal(approval.hubSessionId, "hub-session-approval");
  assert.ok(session);
  assert.equal(session.hubSessionId, "hub-session-approval");


  await adapter.disconnect();
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

test("failed approval response write keeps the request pending for retry", async () => {
  const child = new FakeCodexProcess({approvalOnTurn: true});
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

  child.stdin.destroy();
  await assert.rejects(
    () => adapter.respondToApproval({requestId: event.payload.requestId, decision: "accept"}),
    /codex_app_server_stdin_unavailable/,
  );
  assert.equal(adapter.getHealth().pendingApprovals, 1);

  await adapter.disconnect();
  assert.equal(adapter.getHealth().pendingApprovals, 0);
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
