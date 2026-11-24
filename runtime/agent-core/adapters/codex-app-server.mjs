import {spawn as nodeSpawn} from "node:child_process";
import {createHash, randomUUID} from "node:crypto";
import {createInterface} from "node:readline";
import {createAgentEvent, defineAdapterDescriptor} from "../contract.mjs";

export const CODEX_APP_SERVER_DESCRIPTOR = defineAdapterDescriptor({
  adapterId: "codex.app-server",
  providerId: "codex",
  displayName: "Codex App Server",
  version: "0.1.0",
  mode: "local_runtime",
  authKind: "cli_session",
  capabilities: [
    "message.send",
    "session.create",
    "session.resume",
    "session.interrupt",
    "events.push",
    "approvals.request",
  ],
});

const APPROVAL_METHODS = new Map([
  ["item/commandExecution/requestApproval", "command_execution"],
  ["item/fileChange/requestApproval", "file_change"],
]);
const APPROVAL_DECISIONS = new Set(["accept", "decline", "cancel"]);

function cleanString(value, maxLength = 2000) {
  return typeof value === "string" ? value.slice(0, maxLength) : "";
}

function requiredSessionText(value, field) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > 160) {
    throw new TypeError("invalid_" + field);
  }
  return value.trim();
}

const MESSAGE_DELTA_CHUNK_CODE_UNITS = 4000;
const INLINE_COMPLETION_TEXT_MAX_BYTES = 24 * 1024;

function splitTextLosslessly(value, maxCodeUnits = MESSAGE_DELTA_CHUNK_CODE_UNITS) {
  if (typeof value !== "string") return [];
  if (!value.length) return [];
  const chunks = [];
  for (let offset = 0; offset < value.length;) {
    let end = Math.min(value.length, offset + maxCodeUnits);
    if (end < value.length) {
      const last = value.charCodeAt(end - 1);
      if (last >= 0xd800 && last <= 0xdbff) end -= 1;
    }
    if (end <= offset) end = Math.min(value.length, offset + maxCodeUnits + 1);
    chunks.push(value.slice(offset, end));
    offset = end;
  }
  return chunks;
}

function completionTextMetadata(value) {
  if (typeof value !== "string") {
    return {
      textAvailable: false,
      textIncluded: false,
      textByteLength: null,
      textSha256: null,
    };
  }
  const textByteLength = Buffer.byteLength(value, "utf8");
  const textSha256 = createHash("sha256").update(value, "utf8").digest("hex");
  if (textByteLength <= INLINE_COMPLETION_TEXT_MAX_BYTES) {
    return {
      textAvailable: true,
      textIncluded: true,
      textByteLength,
      textSha256,
      text: value,
    };
  }
  return {
    textAvailable: true,
    textIncluded: false,
    textByteLength,
    textSha256,
  };
}

export class CodexAppServerAdapter {
  #child = null;
  #reader = null;
  #pendingRequests = new Map();
  #pendingApprovals = new Map();
  #hubSessionIds = new Map();
  #pendingSessionStart = null;
  #bufferedSessionEvents = [];
  #sessionCreateInFlight = false;
  #listeners = new Set();
  #nextRequestId = 0;
  #eventSequence = 0;
  #connected = false;
  #closing = false;
  #exitHandled = false;
  #lastError = null;

  constructor({
    command = "codex",
    args = ["app-server"],
    cwd,
    providerId = "codex",
    agentId = "codex-local",
    roomId,
    clientInfo = {name: "pack", title: "Pack", version: "0.1.0"},
    requestTimeoutMs = 30000,
    approvalTimeoutMs = 120000,
    spawnProcess = nodeSpawn,
  } = {}) {
    if (typeof command !== "string" || !command.trim()) throw new TypeError("invalid_codex_command");
    if (!Array.isArray(args) || args.some((item) => typeof item !== "string")) {
      throw new TypeError("invalid_codex_args");
    }
    if (typeof spawnProcess !== "function") throw new TypeError("invalid_spawn_process");
    if (!Number.isFinite(requestTimeoutMs) || requestTimeoutMs <= 0) {
      throw new TypeError("invalid_request_timeout");
    }
    if (!Number.isFinite(approvalTimeoutMs) || approvalTimeoutMs <= 0) {
      throw new TypeError("invalid_approval_timeout");
    }

    this.command = command;
    this.args = [...args];
    this.cwd = cwd;
    this.providerId = providerId;
    this.agentId = agentId;
    this.roomId = roomId;
    this.clientInfo = {...clientInfo};
    this.requestTimeoutMs = requestTimeoutMs;
    this.approvalTimeoutMs = approvalTimeoutMs;
    this.spawnProcess = spawnProcess;
  }

  async connect() {
    if (this.#connected) return this.getHealth();
    if (this.#child) throw new Error("codex_app_server_starting");

    const options = {stdio: ["pipe", "pipe", "pipe"]};
    if (this.cwd) options.cwd = this.cwd;

    this.#exitHandled = false;
    this.#closing = false;
    this.#lastError = null;

    try {
      this.#child = this.spawnProcess(this.command, this.args, options);
    } catch {
      this.#lastError = "codex_app_server_spawn_failed";
      throw new Error(this.#lastError);
    }

    if (!this.#child?.stdin || !this.#child?.stdout) {
      this.#child = null;
      throw new Error("codex_app_server_invalid_process");
    }

    this.#reader = createInterface({input: this.#child.stdout, crlfDelay: Infinity});
    this.#reader.on("line", (line) => this.#onLine(line));
    this.#child.stderr?.on("data", () => {});
    this.#child.on("error", () => this.#handleExit("codex_app_server_process_error"));
    this.#child.on("exit", (code) => {
      this.#handleExit("codex_app_server_exited:" + String(code ?? "unknown"));
    });

    try {
      const result = await this.#request("initialize", {clientInfo: this.clientInfo});
      this.#notify("initialized", {});
      this.#connected = true;
      this.#publish("connection.changed", {status: "connected"});
      return result;
    } catch (error) {
      await this.disconnect();
      throw error;
    }
  }

  async disconnect() {
    this.#closing = true;

    for (const approval of [...this.#pendingApprovals.values()]) {
      clearTimeout(approval.timer);
      try {
        this.#write({id: approval.id, result: "cancel"});
      } catch {}
    }
    this.#pendingApprovals.clear();

    for (const pending of this.#pendingRequests.values()) {
      clearTimeout(pending.timer);
      pending.reject(new Error("codex_app_server_disconnected"));
    }
    this.#pendingRequests.clear();

    this.#reader?.close();
    this.#reader = null;

    const child = this.#child;
    this.#child = null;
    this.#connected = false;
    if (child && child.exitCode == null && !child.killed) {
      try { child.kill("SIGTERM"); } catch {}
    }

    this.#closing = false;
    return {status: "disconnected"};
  }

  async createSession(options = {}) {
    this.#requireConnected();
    if (this.#sessionCreateInFlight) throw new Error("codex_session_create_in_progress");
    const hubSessionId = options.hubSessionId === undefined || options.hubSessionId === null
      ? null
      : requiredSessionText(options.hubSessionId, "hub_session_id");
    if (hubSessionId && options.hubSessionCapabilities?.hubSessionBinding !== true) {
      throw new Error("hub_session_binding_not_negotiated");
    }
    const params = {};
    for (const key of ["model", "cwd", "approvalPolicy", "sandbox", "serviceName"]) {
      if (options[key] !== undefined) params[key] = options[key];
    }

    this.#sessionCreateInFlight = true;
    if (hubSessionId) {
      this.#pendingSessionStart = hubSessionId;
      this.#bufferedSessionEvents = [];
    }
    try {
      const result = await this.#request("thread/start", params);
      const sessionId = result?.thread?.id;
      if (typeof sessionId !== "string" || !sessionId) {
        throw new Error("codex_app_server_missing_thread_id");
      }
      if (hubSessionId) {
        this.#bindHubSession(sessionId, hubSessionId);
        this.#publish(
          "session.created",
          {providerSessionId: sessionId, resumed: false},
          {sessionId, hubSessionId},
        );
        this.#pendingSessionStart = null;
        this.#flushBufferedSessionEvents();
      }
      return {sessionId, providerSessionId: sessionId, thread: result.thread};
    } catch (error) {
      if (hubSessionId) {
        this.#pendingSessionStart = null;
        this.#flushBufferedSessionEvents();
      }
      throw error;
    } finally {
      this.#pendingSessionStart = null;
      this.#sessionCreateInFlight = false;
    }
  }

  async resumeSession({sessionId, hubSessionId, hubSessionCapabilities} = {}) {
    this.#requireConnected();
    if (typeof sessionId !== "string" || !sessionId) throw new TypeError("invalid_codex_session_id");
    const hubId = hubSessionId === undefined || hubSessionId === null
      ? null
      : requiredSessionText(hubSessionId, "hub_session_id");
    if (hubId && hubSessionCapabilities?.hubSessionBinding !== true) {
      throw new Error("hub_session_binding_not_negotiated");
    }
    let addedBinding = false;
    if (hubId) {
      const existing = this.#hubSessionIds.get(sessionId);
      if (existing && existing !== hubId) throw new Error("hub_session_binding_conflict");
      if (!existing) {
        this.#bindHubSession(sessionId, hubId);
        addedBinding = true;
      }
    }
    try {
      const result = await this.#request("thread/resume", {
        threadId: sessionId,
        excludeTurns: true,
      });
      const resumedId = result?.thread?.id;
      if (typeof resumedId !== "string" || !resumedId) {
        throw new Error("codex_app_server_missing_resumed_thread_id");
      }
      if (resumedId !== sessionId) {
        throw new Error("codex_app_server_resumed_session_id_mismatch");
      }
      return {sessionId: resumedId, providerSessionId: resumedId, thread: result.thread};
    } catch (error) {
      if (addedBinding) this.#hubSessionIds.delete(sessionId);
      throw error;
    }
  }

  async sendMessage({sessionId, text, model, cwd, approvalPolicy, sandbox} = {}) {
    this.#requireConnected();
    if (typeof sessionId !== "string" || !sessionId) throw new TypeError("invalid_codex_session_id");
    if (typeof text !== "string" || !text.trim()) throw new TypeError("empty_codex_message");

    const params = {
      threadId: sessionId,
      input: [{type: "text", text}],
    };
    for (const [key, value] of Object.entries({model, cwd, approvalPolicy, sandbox})) {
      if (value !== undefined) params[key] = value;
    }

    const result = await this.#request("turn/start", params);
    const runId = result?.turn?.id;
    if (typeof runId !== "string" || !runId) {
      throw new Error("codex_app_server_missing_turn_id");
    }
    return {
      accepted: true,
      sessionId,
      runId,
      status: result.turn.status ?? "inProgress",
    };
  }

  async interruptSession({sessionId, runId} = {}) {
    this.#requireConnected();
    if (typeof sessionId !== "string" || !sessionId) throw new TypeError("invalid_codex_session_id");
    if (typeof runId !== "string" || !runId) throw new TypeError("invalid_codex_run_id");
    return this.#request("turn/interrupt", {threadId: sessionId, turnId: runId});
  }

  async respondToApproval({requestId, decision} = {}) {
    const approval = this.#pendingApprovals.get(String(requestId));
    if (!approval) throw new Error("codex_approval_not_pending");
    if (!APPROVAL_DECISIONS.has(decision)) throw new TypeError("unsupported_codex_approval_decision");
    if (approval.availableDecisionsProvided && !approval.availableDecisions.includes(decision)) {
      throw new Error("codex_approval_decision_unavailable");
    }

    // Keep the approval pending until the response is actually written.
    // If stdin has closed or the write fails, callers can retry while the
    // timeout remains armed; deleting first would orphan the server request.
    this.#write({id: approval.id, result: decision});
    clearTimeout(approval.timer);
    this.#pendingApprovals.delete(String(requestId));
    return {requestId: String(requestId), decision};
  }

  subscribe(handler) {
    if (typeof handler !== "function") throw new TypeError("invalid_event_handler");
    this.#listeners.add(handler);
    return () => this.#listeners.delete(handler);
  }

  getHealth() {
    return {
      status: this.#connected ? "ready" : (this.#child ? "starting" : "disconnected"),
      pid: this.#child?.pid ?? null,
      pendingRequests: this.#pendingRequests.size,
      pendingApprovals: this.#pendingApprovals.size,
      lastError: this.#lastError,
    };
  }

  #requireConnected() {
    if (!this.#connected || !this.#child) throw new Error("codex_app_server_not_connected");
  }

  #write(message) {
    const child = this.#child;
    if (!child?.stdin || child.stdin.destroyed || child.stdin.writableEnded) {
      throw new Error("codex_app_server_stdin_unavailable");
    }
    child.stdin.write(JSON.stringify(message) + "\n");
  }

  #notify(method, params = {}) {
    this.#write({method, params});
  }

  #request(method, params = {}) {
    this.#requireProcess();
    const id = ++this.#nextRequestId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pendingRequests.delete(id);
        reject(new Error("codex_app_server_request_timeout:" + method));
      }, this.requestTimeoutMs);
      timer.unref?.();
      this.#pendingRequests.set(id, {resolve, reject, timer, method});
      try {
        this.#write({id, method, params});
      } catch {
        clearTimeout(timer);
        this.#pendingRequests.delete(id);
        reject(new Error("codex_app_server_write_failed:" + method));
      }
    });
  }

  #requireProcess() {
    if (!this.#child || this.#exitHandled) throw new Error("codex_app_server_not_running");
  }

  #onLine(line) {
    if (!line.trim()) return;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      this.#lastError = "codex_app_server_invalid_jsonl";
      return;
    }
    if (!message || typeof message !== "object") return;

    if (message.id !== undefined && typeof message.method === "string") {
      this.#handleServerRequest(message);
      return;
    }

    if (message.id !== undefined && this.#pendingRequests.has(message.id)) {
      const pending = this.#pendingRequests.get(message.id);
      this.#pendingRequests.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) {
        pending.reject(new Error("codex_app_server_rpc_error:" + String(message.error.code ?? "unknown")));
      } else {
        pending.resolve(message.result ?? {});
      }
      return;
    }

    if (typeof message.method === "string") this.#handleNotification(message);
  }

  #handleServerRequest(message) {
    const kind = APPROVAL_METHODS.get(message.method);
    if (!kind) {
      try {
        this.#write({
          id: message.id,
          error: {code: -32601, message: "Unsupported server request"},
        });
      } catch {}
      return;
    }

    const params = message.params ?? {};
    const requestId = String(message.id);
    const availableDecisionsProvided = Array.isArray(params.availableDecisions);
    const availableDecisions = availableDecisionsProvided
      ? params.availableDecisions.filter((item) => APPROVAL_DECISIONS.has(item))
      : [];

    const approval = {
      id: message.id,
      kind,
      threadId: params.threadId,
      turnId: params.turnId,
      itemId: params.itemId,
      availableDecisions,
      availableDecisionsProvided,
      timer: null,
    };
    approval.timer = setTimeout(() => {
      this.respondToApproval({requestId, decision: "cancel"}).catch(() => {});
    }, this.approvalTimeoutMs);
    approval.timer.unref?.();
    this.#pendingApprovals.set(requestId, approval);

    const payload = {
      requestId,
      approvalKind: kind,
      itemId: cleanString(params.itemId, 160),
      reason: cleanString(params.reason, 1000),
      availableDecisions,
    };
    const eventOptions = {
      sessionId: params.threadId,
      runId: params.turnId,
    };
    if (this.#pendingSessionStart && eventOptions.sessionId) {
      this.#bufferedSessionEvents.push({
        kind: "event",
        eventType: "approval.requested",
        payload,
        options: eventOptions,
      });
    } else {
      this.#publish("approval.requested", payload, eventOptions);
    }
  }

  #handleNotification(message) {
    const params = message.params ?? {};
    const sessionId = params.threadId ?? params.turn?.threadId;
    const runId = params.turnId ?? params.turn?.id;
    if (this.#pendingSessionStart && sessionId) {
      this.#bufferedSessionEvents.push({kind: "notification", message});
      return;
    }

    switch (message.method) {
      case "turn/started":
        this.#publish("run.started", {status: params.turn?.status ?? "inProgress"}, {sessionId, runId});
        break;
      case "turn/completed": {
        const status = params.turn?.status ?? "failed";
        const eventType = status === "completed"
          ? "run.completed"
          : (status === "interrupted" ? "run.interrupted" : "run.failed");
        this.#publish(eventType, {status}, {sessionId, runId});
        break;
      }
      case "item/agentMessage/delta": {
        if (typeof params.delta !== "string") break;
        const itemId = cleanString(params.itemId, 160);
        for (const delta of splitTextLosslessly(params.delta)) {
          this.#publish("message.delta", {itemId, delta}, {sessionId, runId});
        }
        break;
      }
      case "item/completed":
        if (params.item?.type === "agentMessage") {
          this.#publish("message.completed", {
            itemId: cleanString(params.item.id, 160),
            ...completionTextMetadata(params.item.text),
            phase: cleanString(params.item.phase, 64),
          }, {sessionId, runId});
        } else {
          this.#publish("run.progress", {
            itemId: cleanString(params.item?.id, 160),
            itemType: cleanString(params.item?.type, 80),
            itemStatus: cleanString(params.item?.status, 80),
          }, {sessionId, runId});
        }
        break;
      case "item/started":
        this.#publish("run.progress", {
          itemId: cleanString(params.item?.id, 160),
          itemType: cleanString(params.item?.type, 80),
          itemStatus: cleanString(params.item?.status, 80),
        }, {sessionId, runId});
        break;
      case "serverRequest/resolved": {
        const requestId = String(params.requestId ?? "");
        const approval = this.#pendingApprovals.get(requestId);
        if (approval) clearTimeout(approval.timer);
        this.#pendingApprovals.delete(requestId);
        break;
      }
      default:
        break;
    }
  }

  #flushBufferedSessionEvents() {
    const buffered = this.#bufferedSessionEvents;
    this.#bufferedSessionEvents = [];
    for (const item of buffered) {
      if (item.kind === "notification") {
        this.#handleNotification(item.message);
      } else if (item.kind === "event") {
        if (item.eventType === "approval.requested"
            && !this.#pendingApprovals.has(String(item.payload?.requestId ?? ""))) {
          continue;
        }
        this.#publish(item.eventType, item.payload, item.options);
      }
    }
  }

  #bindHubSession(sessionId, hubSessionId) {
    const existing = this.#hubSessionIds.get(sessionId);
    if (existing && existing !== hubSessionId) {
      throw new Error("hub_session_binding_conflict");
    }
    this.#hubSessionIds.set(sessionId, hubSessionId);
  }

  #publish(eventType, payload, {sessionId, runId, hubSessionId} = {}) {
    const input = {
      eventId: randomUUID(),
      eventType,
      providerId: this.providerId,
      agentId: this.agentId,
      sequence: ++this.#eventSequence,
      occurredAt: new Date().toISOString(),
      payload,
    };
    if (this.roomId) input.roomId = this.roomId;
    if (sessionId) input.sessionId = sessionId;
    if (runId) input.runId = runId;
    const resolvedHubSessionId = hubSessionId
      ?? (sessionId ? this.#hubSessionIds.get(sessionId) : null);
    if (resolvedHubSessionId) input.hubSessionId = resolvedHubSessionId;

    try {
      const event = createAgentEvent(input);
      for (const handler of this.#listeners) {
        try { handler(event); } catch {}
      }
    } catch (error) {
      this.#lastError = String(error?.message ?? "codex_event_invalid");
    }
  }

  #handleExit(reason) {
    if (this.#exitHandled) return;
    this.#exitHandled = true;
    this.#connected = false;
    this.#lastError = reason;
    this.#child = null;
    this.#reader?.close();
    this.#reader = null;

    for (const pending of this.#pendingRequests.values()) {
      clearTimeout(pending.timer);
      pending.reject(new Error(reason));
    }
    this.#pendingRequests.clear();

    for (const approval of this.#pendingApprovals.values()) clearTimeout(approval.timer);
    this.#pendingApprovals.clear();

    if (!this.#closing) this.#publish("connection.changed", {status: "disconnected", reason});
  }
}

export function registerCodexAppServerAdapter(registry, options = {}) {
  return registry.register({
    descriptor: CODEX_APP_SERVER_DESCRIPTOR,
    create: async (context = {}) => new CodexAppServerAdapter({...options, ...context}),
  });
}