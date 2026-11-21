const PRIVATE_KEYS = new Set([
  "apiKey",
  "accessToken",
  "refreshToken",
  "authorization",
  "password",
  "cookie",
  "cookies",
  "nativeMemory",
  "privateMemory",
  "privateContext",
  "credentials",
]);

function isObject(value) {
  return value !== null && typeof value === "object";
}

function assertNoPrivateFields(value, path = "input") {
  if (!isObject(value)) return;
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertNoPrivateFields(item, path + "[" + index + "]"));
    return;
  }
  for (const [key, nested] of Object.entries(value)) {
    if (PRIVATE_KEYS.has(key)) throw new Error("private_context_forbidden:" + path + "." + key);
    assertNoPrivateFields(nested, path + "." + key);
  }
}

function text(value, field, maxLength, allowEmpty = false) {
  if (typeof value !== "string" || value.length > maxLength || (!allowEmpty && !value.trim())) {
    throw new TypeError("invalid_context_" + field);
  }
  return value;
}

function identifier(value, field) {
  if (value === undefined || value === null) throw new TypeError("invalid_context_" + field);
  return text(String(value), field, 160);
}

function boundedArray(value, field, maxItems) {
  if (value == null) return [];
  if (!Array.isArray(value) || value.length > maxItems) throw new TypeError("invalid_context_" + field);
  return value;
}

function sharedRecord(record, roomId, kind) {
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    throw new TypeError("invalid_context_" + kind);
  }
  if (record.roomId !== roomId) throw new Error("cross_room_context_denied:" + kind);
  if (record.visibility !== "shared") throw new Error("non_shared_context_denied:" + kind);
  return record;
}

export function createContextPackage(input = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new TypeError("invalid_context_package");
  }
  assertNoPrivateFields(input);

  const roomId = text(input.roomId, "room_id", 160);
  const agentId = text(input.agentId, "agent_id", 160);
  const request = text(input.request, "request", 20000);
  if (!request.trim()) throw new TypeError("incomplete_context_package");

  const messages = boundedArray(input.messages, "messages", 100).map((item) => {
    const record = sharedRecord(item, roomId, "message");
    return Object.freeze({
      id: identifier(record.id, "message_id"),
      author: text(record.author, "message_author", 160),
      kind: text(record.kind, "message_kind", 64),
      body: text(record.body, "message_body", 8000, true),
      createdAt: record.createdAt == null ? null : text(record.createdAt, "message_created_at", 64),
    });
  });

  const roomSummary = input.roomSummary == null ? null : (() => {
    const record = sharedRecord(input.roomSummary, roomId, "summary");
    const list = (value, field) => Object.freeze(
      boundedArray(value, field, 50).map((item) => text(item, field + "_item", 1000))
    );
    return Object.freeze({
      decided: list(record.decided, "decided"),
      inProgress: list(record.inProgress, "inProgress"),
      pending: list(record.pending, "pending"),
    });
  })();

  const attachments = boundedArray(input.attachments, "attachments", 50).map((item) => {
    const record = sharedRecord(item, roomId, "attachment");
    return Object.freeze({
      id: identifier(record.id, "attachment_id"),
      name: text(record.name, "attachment_name", 255),
      mimeType: text(record.mimeType, "attachment_mime_type", 128),
      reference: text(record.reference, "attachment_reference", 512),
    });
  });

  const task = input.task == null ? null : (() => {
    const record = sharedRecord(input.task, roomId, "task");
    return Object.freeze({
      id: identifier(record.id, "task_id"),
      title: text(record.title, "task_title", 500),
      body: text(record.body, "task_body", 20000, true),
      status: text(record.status, "task_status", 64),
    });
  })();

  return Object.freeze({
    version: 1,
    scope: "room-shared",
    roomId,
    agentId,
    request,
    roomSummary,
    messages: Object.freeze(messages),
    attachments: Object.freeze(attachments),
    task,
  });
}