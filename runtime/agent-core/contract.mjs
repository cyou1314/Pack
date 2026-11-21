import {findSensitiveField} from "./sensitive-fields.mjs";
const MODES = new Set([
  "native_account",
  "local_runtime",
  "official_api",
  "mcp_connector",
  "external_agent",
  "browser_bridge",
]);

const AUTH_KINDS = new Set([
  "none",
  "oauth",
  "api_key",
  "cli_session",
  "mcp_connector",
  "local_process",
]);

export const ADAPTER_MODES = Object.freeze([...MODES]);
export const AUTHENTICATION_KINDS = Object.freeze([...AUTH_KINDS]);

export const AGENT_CAPABILITIES = Object.freeze([
  "message.send",
  "message.stream",
  "session.create",
  "session.resume",
  "session.interrupt",
  "events.push",
  "tools.invoke",
  "approvals.request",
  "attachments.read",
  "attachments.write",
  "memory.native",
  "memory.pack",
  "browser.control",
  "computer.control",
  "usage.read",
]);

export const AGENT_EVENT_TYPES = Object.freeze([
  "connection.changed",
  "session.created",
  "message.delta",
  "message.completed",
  "run.started",
  "run.progress",
  "run.completed",
  "run.failed",
  "run.interrupted",
  "tool.started",
  "tool.completed",
  "approval.requested",
  "usage.updated",
]);

const CAPABILITIES = new Set(AGENT_CAPABILITIES);
const EVENT_TYPES = new Set(AGENT_EVENT_TYPES);




function assertNoSensitiveFields(value, path = "payload") {
  const found = findSensitiveField(value, path);
  if (found) throw new Error("sensitive_field_forbidden:" + found.path + "." + found.key);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function requiredText(value, field, maxLength = 128) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > maxLength) {
    throw new TypeError("invalid_" + field);
  }
  return value.trim();
}

export function defineAdapterDescriptor(input) {
  if (!isPlainObject(input)) throw new TypeError("invalid_adapter_descriptor");

  const adapterId = requiredText(input.adapterId, "adapter_id");
  const providerId = requiredText(input.providerId, "provider_id");
  const displayName = requiredText(input.displayName, "display_name");
  const version = requiredText(input.version, "adapter_version", 32);
  const mode = requiredText(input.mode, "adapter_mode", 32);
  const authKind = requiredText(input.authKind, "auth_kind", 32);

  if (!/^[a-z0-9][a-z0-9._-]*$/.test(adapterId)) throw new TypeError("invalid_adapter_id");
  if (!/^[a-z0-9][a-z0-9._-]*$/.test(providerId)) throw new TypeError("invalid_provider_id");
  if (!MODES.has(mode)) throw new TypeError("unsupported_adapter_mode");
  if (!AUTH_KINDS.has(authKind)) throw new TypeError("unsupported_auth_kind");

  const rawCapabilities = input.capabilities ?? [];
  if (!Array.isArray(rawCapabilities)) throw new TypeError("invalid_capabilities");
  const capabilities = [...new Set(rawCapabilities.map((item) => requiredText(item, "capability", 64)))].sort();
  for (const capability of capabilities) {
    if (!CAPABILITIES.has(capability)) throw new TypeError("unsupported_capability:" + capability);
  }

  return Object.freeze({
    adapterId,
    providerId,
    displayName,
    version,
    mode,
    authKind,
    capabilities: Object.freeze(capabilities),
  });
}

export function createAgentEvent(input) {
  if (!isPlainObject(input)) throw new TypeError("invalid_agent_event");
  const eventId = requiredText(input.eventId, "event_id", 160);
  const eventType = requiredText(input.eventType, "event_type", 64);
  const providerId = requiredText(input.providerId, "provider_id");
  const agentId = requiredText(input.agentId, "agent_id");
  const occurredAt = requiredText(input.occurredAt, "occurred_at", 64);
  const sequence = input.sequence ?? 0;
  const payload = input.payload ?? {};
  assertNoSensitiveFields(payload, "event.payload");

  if (!EVENT_TYPES.has(eventType)) throw new TypeError("unsupported_event_type");
  if (!Number.isFinite(Date.parse(occurredAt))) throw new TypeError("invalid_occurred_at");
  if (!Number.isSafeInteger(sequence) || sequence < 0) throw new TypeError("invalid_event_sequence");
  if (!isPlainObject(payload)) throw new TypeError("invalid_event_payload");

  const event = {
    eventId,
    eventType,
    providerId,
    agentId,
    sequence,
    occurredAt: new Date(occurredAt).toISOString(),
    payload: Object.freeze({...payload}),
  };

  for (const field of ["roomId", "sessionId", "runId"]) {
    if (input[field] !== undefined && input[field] !== null) {
      event[field] = requiredText(input[field], field, 160);
    }
  }

  return Object.freeze(event);
}

const CAPABILITY_METHODS = Object.freeze({
  "session.create": "createSession",
  "session.resume": "resumeSession",
  "session.interrupt": "interruptSession",
  "events.push": "subscribe",
  "approvals.request": "respondToApproval",
});

export function assertAdapterInstance(descriptor, instance) {
  if (!descriptor || !isPlainObject(descriptor)) throw new TypeError("invalid_adapter_descriptor");
  if (!instance || typeof instance !== "object") throw new TypeError("invalid_adapter_instance");

  for (const method of ["connect", "disconnect", "sendMessage", "getHealth"]) {
    if (typeof instance[method] !== "function") {
      throw new TypeError("adapter_missing_method:" + method);
    }
  }

  for (const capability of descriptor.capabilities ?? []) {
    const method = CAPABILITY_METHODS[capability];
    if (method && typeof instance[method] !== "function") {
      throw new TypeError("adapter_capability_method_missing:" + capability);
    }
  }

  return instance;
}