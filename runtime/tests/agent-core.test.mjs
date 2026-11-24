import test from "node:test";
import assert from "node:assert/strict";
import {
  defineAdapterDescriptor,
  createAgentEvent,
  assertAdapterInstance,
} from "../agent-core/contract.mjs";
import {AgentAdapterRegistry} from "../agent-core/registry.mjs";
import {createContextPackage} from "../agent-core/context-package.mjs";

const descriptorInput = {
  adapterId: "codex.app-server",
  providerId: "codex",
  displayName: "Codex App Server",
  version: "0.1.0",
  mode: "local_runtime",
  authKind: "cli_session",
  capabilities: ["message.send", "session.create", "session.resume", "events.push"],
};

function fakeAdapter() {
  return {
    async connect() {},
    async disconnect() {},
    async sendMessage() { return {accepted: true}; },
    async getHealth() { return {status: "ready"}; },
    async createSession() { return {sessionId: "s-1"}; },
    async resumeSession() { return {sessionId: "s-1"}; },
    async subscribe() { return () => {}; },
  };
}

test("adapter descriptor validates mode, auth kind and capabilities", () => {
  const descriptor = defineAdapterDescriptor(descriptorInput);
  assert.equal(descriptor.providerId, "codex");
  assert.equal(descriptor.mode, "local_runtime");
  assert.deepEqual(descriptor.capabilities, [
    "events.push",
    "message.send",
    "session.create",
    "session.resume",
  ]);
  assert.throws(
    () => defineAdapterDescriptor({...descriptorInput, mode: "browser_magic"}),
    /unsupported_adapter_mode/,
  );
  assert.throws(
    () => defineAdapterDescriptor({...descriptorInput, capabilities: ["unverified.superpower"]}),
    /unsupported_capability/,
  );
});

test("adapter instance must implement its declared capabilities", () => {
  const descriptor = defineAdapterDescriptor(descriptorInput);
  assert.doesNotThrow(() => assertAdapterInstance(descriptor, fakeAdapter()));
  const incomplete = fakeAdapter();
  delete incomplete.resumeSession;
  assert.throws(
    () => assertAdapterInstance(descriptor, incomplete),
    /adapter_capability_method_missing:session.resume/,
  );
});

test("registry rejects duplicates and creates validated instances", async () => {
  const registry = new AgentAdapterRegistry();
  registry.register({descriptor: descriptorInput, create: async () => fakeAdapter()});
  assert.equal(registry.size, 1);
  assert.equal(registry.get("codex.app-server").providerId, "codex");
  assert.equal(registry.list({mode: "local_runtime"}).length, 1);
  assert.equal(registry.list({providerId: "openai"}).length, 0);
  await assert.doesNotReject(() => registry.create("codex.app-server"));
  assert.throws(
    () => registry.register({descriptor: descriptorInput, create: async () => fakeAdapter()}),
    /adapter_already_registered/,
  );
  await assert.rejects(() => registry.create("missing.adapter"), /adapter_not_found/);
});

test("Agent Event validates identity, type, timestamp and sequence", () => {
  const event = createAgentEvent({
    eventId: "evt-1",
    eventType: "message.delta",
    providerId: "codex",
    agentId: "agent-codex",
    roomId: "pack-main",
    sessionId: "thread-1",
    runId: "run-1",
    sequence: 2,
    occurredAt: "2026-11-21T08:00:00.000Z",
    payload: {text: "hello"},
  });
  assert.equal(event.eventType, "message.delta");
  assert.equal(event.sequence, 2);
  assert.equal(event.roomId, "pack-main");
  assert.throws(
    () => createAgentEvent({
      eventId: "evt-2",
      eventType: "unknown.event",
      providerId: "codex",
      agentId: "agent-codex",
      occurredAt: "2026-11-21T08:00:00.000Z",
    }),
    /unsupported_event_type/,
  );
  assert.throws(
    () => createAgentEvent({
      eventId: "evt-3",
      eventType: "run.started",
      providerId: "codex",
      agentId: "agent-codex",
      sequence: -1,
      occurredAt: "2026-11-21T08:00:00.000Z",
    }),
    /invalid_event_sequence/,
  );
});

test("Context Package includes only explicitly shared data from its room", () => {
  const result = createContextPackage({
    roomId: "pack-main",
    agentId: "agent-codex",
    request: "Review the proposed change.",
    roomSummary: {
      roomId: "pack-main",
      visibility: "shared",
      decided: ["Keep the existing Hub"],
      inProgress: ["Build the Agent Core"],
      pending: [],
      privateNote: "must not be copied",
    },
    messages: [{
      id: 7,
      roomId: "pack-main",
      visibility: "shared",
      author: "owner",
      kind: "chat",
      body: "Please review this plan.",
      createdAt: "2026-11-21T08:00:00.000Z",
      internalDebug: "not copied",
    }],
    attachments: [{
      id: "file-1",
      roomId: "pack-main",
      visibility: "shared",
      name: "plan.md",
      mimeType: "text/markdown",
      reference: "artifact:file-1",
      localPath: "/private/path/not-copied",
    }],
  });

  assert.equal(result.scope, "room-shared");
  assert.equal(result.messages[0].body, "Please review this plan.");
  assert.equal(result.messages[0].internalDebug, undefined);
  assert.equal(result.attachments[0].localPath, undefined);
  assert.deepEqual(result.roomSummary.decided, ["Keep the existing Hub"]);
  assert.equal("nativeMemory" in result, false);
});

test("Context Package rejects cross-room and private context", () => {
  assert.throws(
    () => createContextPackage({
      roomId: "room-a",
      agentId: "agent-1",
      request: "Review",
      messages: [{
        id: 1,
        roomId: "room-b",
        visibility: "shared",
        author: "owner",
        kind: "chat",
        body: "cross-room",
      }],
    }),
    /cross_room_context_denied/,
  );

  assert.throws(
    () => createContextPackage({
      roomId: "room-a",
      agentId: "agent-1",
      request: "Review",
      nativeMemory: "private",
    }),
    /private_context_forbidden/,
  );

  assert.throws(
    () => createContextPackage({
      roomId: "room-a",
      agentId: "agent-1",
      request: "Review",
      messages: [{
        id: 1,
        roomId: "room-a",
        visibility: "private",
        author: "agent",
        kind: "chat",
        body: "private",
      }],
    }),
    /non_shared_context_denied/,
  );

  assert.throws(
    () => createContextPackage({
      roomId: "room-a",
      agentId: "agent-1",
      request: "Review",
      messages: [{
        id: 1,
        roomId: "room-a",
        visibility: "shared",
        author: "agent",
        kind: "chat",
        body: "private",
        credentials: {apiKey: "must-not-pass"},
      }],
    }),
    /private_context_forbidden/,
  );
});

test("Agent Event and Context Package reject snake_case secrets", () => {
  assert.throws(
    () => createAgentEvent({
      eventId: "evt-secret",
      eventType: "message.completed",
      providerId: "openai",
      agentId: "agent-openai",
      occurredAt: "2026-11-21T08:00:00.000Z",
      payload: {nested: {api_key: "must-not-enter-event"}},
    }),
    /sensitive_field_forbidden/,
  );
  assert.throws(
    () => createContextPackage({
      roomId: "room-a",
      agentId: "agent-1",
      request: "Review",
      messages: [{
        id: 1,
        roomId: "room-a",
        visibility: "shared",
        author: "owner",
        kind: "chat",
        body: "safe-looking message",
        refresh_token: "must-not-enter-context",
      }],
    }),
    /private_context_forbidden/,
  );
});


test("Context Package rejects credential and cookie aliases", () => {
  const forbiddenKeys = [
    "sessionCookie", "cookieJar", "oauth_token", "idToken",
    "authToken", "bearer_token", "secret_key", "ssh_private_key",
    "credential", "x_api_key", "authorization_header", "secret_token",
    "private_key_pem", "session_token", "\\uFF41\\uFF50\\uFF49\\uFF3F\\uFF4B\\uFF45\\uFF59",
  ];
  for (const key of forbiddenKeys) {
    assert.throws(
      () => createAgentEvent({
        eventId: "evt-" + key,
        eventType: "message.completed",
        providerId: "openai",
        agentId: "agent-openai",
        occurredAt: "2026-11-21T08:00:00.000Z",
        payload: {[key]: "must-not-enter-event"},
      }),
      /sensitive_field_forbidden/,
      "expected sensitive event field to be rejected: " + key,
    );
    assert.throws(
      () => createContextPackage({
        roomId: "room-a",
        agentId: "agent-1",
        request: "Review",
        messages: [{
          id: 1,
          roomId: "room-a",
          visibility: "shared",
          author: "owner",
          kind: "chat",
          body: "safe-looking message",
          [key]: "must-not-enter-context",
        }],
      }),
      /private_context_forbidden/,
      "expected sensitive field to be rejected: " + key,
    );
  }
});
