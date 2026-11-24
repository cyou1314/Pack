import {createAgentEvent} from "./contract.mjs";

const APPROVAL_DECISIONS = new Set(["accept", "decline", "cancel"]);
const SESSION_CAPABILITIES = new Set(["session.create", "session.resume"]);

function requiredText(value, field, maxLength = 160) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > maxLength) {
    throw new TypeError("invalid_" + field);
  }
  return value.trim();
}

function safeErrorCode(error) {
  const message = error instanceof Error ? error.message : "";
  return /^[a-z0-9_.:-]{1,80}$/i.test(message) ? message : "runtime_error";
}

/**
 * Owns Provider Adapter instances and serializes their lifecycle and event flow.
 *
 * eventStore.append(event) must be idempotent by eventId and resolve only after
 * the event is durably committed. The supervisor does not provide a production
 * store or Hub transport; callers must inject those explicitly.
 */
export class RuntimeSupervisor {
  #registry;
  #eventStore;
  #authorizeApproval;
  #agents = new Map();
  #listeners = new Set();
  #stopping = false;

  constructor({registry, eventStore, authorizeApproval} = {}) {
    if (!registry || typeof registry.create !== "function" || typeof registry.get !== "function") {
      throw new TypeError("adapter_registry_required");
    }
    if (!eventStore || typeof eventStore.append !== "function") {
      throw new TypeError("durable_event_store_required");
    }
    if (authorizeApproval !== undefined && typeof authorizeApproval !== "function") {
      throw new TypeError("approval_authorizer_must_be_function");
    }
    this.#registry = registry;
    this.#eventStore = eventStore;
    this.#authorizeApproval = authorizeApproval;
  }

  async connectAgent({agentId, adapterId, context = {}} = {}) {
    if (this.#stopping) throw new Error("runtime_supervisor_stopping");
    agentId = requiredText(agentId, "agent_id");
    adapterId = requiredText(adapterId, "adapter_id", 128);
    if (!context || typeof context !== "object" || Array.isArray(context)) {
      throw new TypeError("invalid_adapter_context");
    }

    let handle = this.#agents.get(agentId);
    if (handle?.adapterId && handle.adapterId !== adapterId) {
      throw new Error("agent_adapter_conflict");
    }
    if (handle?.disconnectFailed) throw new Error("agent_disconnect_failed");
    if (handle?.disconnectPromise) throw new Error("agent_lifecycle_busy");
    if (handle?.connectPromise) return handle.connectPromise;
    if (handle?.status === "ready" && !handle.eventQueue.length && !handle.fatalEventError) {
      return this.#snapshot(handle);
    }

    if (!handle) {
      handle = {
        agentId,
        adapterId,
        descriptor: null,
        adapter: null,
        status: "disconnected",
        targetStatus: "ready",
        connectionEstablished: false,
        startupFailed: false,
        disconnectFailed: false,
        lastError: null,
        fatalEventError: false,
        sessions: new Set(),
        pendingApprovals: new Map(),
        approvalInFlight: new Set(),
        inFlightCommands: 0,
        commandWaiters: [],
        eventQueue: [],
        drainPromise: null,
        unsubscribe: null,
        connectPromise: null,
        disconnectPromise: null,
      };
      this.#agents.set(agentId, handle);
    }

    handle.adapterId = adapterId;
    handle.connectPromise = this.#connectAgentInternal(handle, context);
    try {
      return await handle.connectPromise;
    } finally {
      handle.connectPromise = null;
    }
  }

  async #connectAgentInternal(handle, context) {
    if (this.#stopping) throw new Error("runtime_supervisor_stopping");

    if (handle.status === "ready") {
      const flushed = await this.#drainEvents(handle);
      if (!flushed) throw new Error(handle.lastError || "event_persistence_failed");
      if (this.#stopping || handle.disconnectPromise) throw new Error("agent_lifecycle_busy");
      if (handle.status === "ready") return this.#snapshot(handle);
    }
    if (handle.status === "degraded") {
      const recovered = await this.#drainEvents(handle);
      if (!recovered) throw new Error(handle.lastError || "event_persistence_failed");
      if (this.#stopping || handle.disconnectPromise) throw new Error("agent_lifecycle_busy");
      if (handle.status === "ready") return this.#snapshot(handle);
    }
    if (handle.eventQueue.length || handle.fatalEventError) {
      const recovered = await this.#drainEvents(handle);
      if (!recovered) throw new Error(handle.lastError || "event_persistence_failed");
      if (this.#stopping || handle.disconnectPromise) throw new Error("agent_lifecycle_busy");
    }
    if (this.#stopping || handle.disconnectPromise) throw new Error("agent_lifecycle_busy");
    if (handle.status === "disconnecting" || handle.status === "connecting") {
      throw new Error("agent_lifecycle_busy");
    }

    if (handle.status === "disconnected" && handle.adapter) {
      handle.targetStatus = "disconnected";
      handle.status = "disconnecting";
      const staleUnsubscribe = handle.unsubscribe;
      handle.adapter = null;
      handle.connectionEstablished = false;
      handle.sessions.clear();
      handle.pendingApprovals.clear();
      handle.approvalInFlight.clear();
      if (handle.unsubscribe === staleUnsubscribe) {
        try { staleUnsubscribe?.(); } catch {}
        handle.unsubscribe = null;
      }
      const flushed = await this.#drainEvents(handle);
      if (!flushed) throw new Error(handle.lastError || "event_persistence_failed");
      handle.status = "disconnected";
      if (this.#stopping || handle.disconnectPromise) throw new Error("agent_lifecycle_busy");
    }

    if (this.#stopping || handle.disconnectPromise) throw new Error("agent_lifecycle_busy");
    handle.status = "connecting";
    handle.targetStatus = "connecting";
    handle.connectionEstablished = false;
    handle.startupFailed = false;
    handle.disconnectFailed = false;
    handle.lastError = null;
    handle.fatalEventError = false;
    handle.sessions.clear();
    handle.pendingApprovals.clear();
    handle.approvalInFlight.clear();

    const snapshot = await this.#connectHandle(handle, context);
    if (this.#stopping || handle.disconnectPromise) throw new Error("agent_lifecycle_busy");
    return snapshot;
  }

  async #connectHandle(handle, context) {
    try {
      const descriptor = this.#registry.get(handle.adapterId);
      if (!descriptor) throw new Error("adapter_not_found");
      if (!descriptor.capabilities.includes("events.push")) {
        throw new Error("adapter_event_stream_required");
      }
      handle.descriptor = descriptor;
      handle.adapter = await this.#registry.create(handle.adapterId, {
        ...context,
        agentId: handle.agentId,
      });
      handle.unsubscribe = handle.adapter.subscribe((event) => this.#enqueueEvent(handle, event));
      await handle.adapter.connect();

      const health = handle.adapter.getHealth();
      if (health?.status !== "ready") throw new Error("adapter_not_ready");
      handle.connectionEstablished = true;
      handle.startupFailed = false;
      handle.disconnectFailed = false;
      handle.targetStatus = "ready";

      if (handle.status === "degraded") {
        throw new Error(handle.lastError || "event_persistence_failed");
      }
      const flushed = await this.#drainEvents(handle);
      if (!flushed) throw new Error(handle.lastError || "event_persistence_failed");

      handle.status = "ready";
      handle.targetStatus = "ready";
      handle.lastError = null;
      return this.#snapshot(handle);
    } catch (error) {
      if (handle.connectionEstablished && handle.status === "degraded") {
        handle.targetStatus = "ready";
        throw new Error(handle.lastError || "event_persistence_failed");
      }

      handle.startupFailed = true;
      handle.targetStatus = "failed";
      let cleanupError = null;
      try { await handle.adapter?.disconnect?.(); }
      catch (disconnectError) { cleanupError = disconnectError; }

      if (cleanupError) {
        handle.disconnectFailed = true;
        handle.targetStatus = "failed";
        const flushed = await this.#drainEvents(handle);
        if (!flushed) throw new Error(handle.lastError || "event_persistence_failed");
        handle.status = "failed";
        handle.lastError = safeErrorCode(cleanupError);
        throw new Error(handle.lastError);
      }

      handle.disconnectFailed = false;
      const flushed = await this.#drainEvents(handle);
      try { handle.unsubscribe?.(); } catch {}
      handle.unsubscribe = null;
      handle.adapter = null;
      handle.connectionEstablished = false;

      if (!flushed || handle.status === "degraded") {
        if (handle.status !== "degraded") {
          handle.status = "degraded";
          handle.lastError = "event_persistence_failed";
        }
        throw new Error(handle.lastError || "event_persistence_failed");
      }
      handle.status = "failed";
      handle.lastError = safeErrorCode(error);
      throw new Error(handle.lastError);
    }
  }

  async createSession(agentId, options = {}) {
    return this.#withReadyAgent(agentId, async (handle) => {
      if (!handle.descriptor.capabilities.includes("session.create")) {
        throw new Error("adapter_capability_unavailable:session.create");
      }
      const result = await handle.adapter.createSession(options);
      const sessionId = requiredText(result?.sessionId, "session_id");
      handle.sessions.add(sessionId);
      await this.#flushOrThrow(handle);
      return {...result, sessionId};
    });
  }

  async resumeSession(agentId, options = {}) {
    return this.#withReadyAgent(agentId, async (handle) => {
      if (!handle.descriptor.capabilities.includes("session.resume")) {
        throw new Error("adapter_capability_unavailable:session.resume");
      }
      const result = await handle.adapter.resumeSession(options);
      const sessionId = requiredText(result?.sessionId, "session_id");
      handle.sessions.add(sessionId);
      await this.#flushOrThrow(handle);
      return {...result, sessionId};
    });
  }

  async sendMessage({agentId, sessionId, text, ...options} = {}) {
    return this.#withReadyAgent(agentId, async (handle) => {
      if (typeof text !== "string" || !text.trim()) throw new TypeError("invalid_message_text");
      const managedSession = sessionId === undefined || sessionId === null
        ? ""
        : requiredText(sessionId, "session_id");
      if (handle.descriptor.capabilities.some((item) => SESSION_CAPABILITIES.has(item))) {
        if (!managedSession || !handle.sessions.has(managedSession)) {
          throw new Error("session_not_managed");
        }
      }
      const result = await handle.adapter.sendMessage({
        ...options,
        ...(managedSession ? {sessionId: managedSession} : {}),
        text,
      });
      await this.#flushOrThrow(handle);
      return result;
    });
  }

  async interruptSession({agentId, sessionId, runId} = {}) {
    return this.#withReadyAgent(agentId, async (handle) => {
      if (!handle.descriptor.capabilities.includes("session.interrupt")) {
        throw new Error("adapter_capability_unavailable:session.interrupt");
      }
      const managedSession = requiredText(sessionId, "session_id");
      if (!handle.sessions.has(managedSession)) throw new Error("session_not_managed");
      const result = await handle.adapter.interruptSession({
        sessionId: managedSession,
        ...(runId ? {runId: requiredText(runId, "run_id")} : {}),
      });
      await this.#flushOrThrow(handle);
      return result;
    });
  }

  async respondToApproval({agentId, requestId, decision} = {}) {
    return this.#withReadyAgent(agentId, async (handle) => {
      if (!handle.descriptor.capabilities.includes("approvals.request")) {
        throw new Error("adapter_capability_unavailable:approvals.request");
      }
      requestId = requiredText(requestId, "approval_request_id");
      decision = requiredText(decision, "approval_decision", 32);
      if (!APPROVAL_DECISIONS.has(decision)) throw new Error("unsupported_approval_decision");
      if (handle.approvalInFlight.has(requestId)) throw new Error("approval_response_in_progress");

      const approvalEvent = handle.pendingApprovals.get(requestId);
      if (!approvalEvent) throw new Error("approval_not_pending");
      if (!this.#authorizeApproval) throw new Error("approval_service_unavailable");

      const adapter = handle.adapter;
      handle.approvalInFlight.add(requestId);
      try {
        let authorized = false;
        try {
          authorized = await this.#authorizeApproval({
            agentId: handle.agentId,
            requestId,
            decision,
            event: approvalEvent,
          });
        } catch {
          throw new Error("approval_authorization_failed");
        }
        if (authorized !== true) throw new Error("approval_not_authorized");

        if (
          this.#agents.get(handle.agentId) !== handle ||
          handle.adapter !== adapter ||
          handle.status !== "ready" ||
          handle.eventQueue.length ||
          handle.pendingApprovals.get(requestId) !== approvalEvent
        ) {
          throw new Error("agent_not_ready");
        }

        const result = await adapter.respondToApproval({requestId, decision});
        handle.pendingApprovals.delete(requestId);
        await this.#flushOrThrow(handle);
        return result;
      } finally {
        handle.approvalInFlight.delete(requestId);
      }
    });
  }

  async disconnectAgent(agentId) {
    agentId = requiredText(agentId, "agent_id");
    const handle = this.#agents.get(agentId);
    if (!handle) return null;
    if (handle.disconnectPromise) return handle.disconnectPromise;

    handle.disconnectPromise = this.#disconnectHandle(handle);
    try {
      return await handle.disconnectPromise;
    } finally {
      handle.disconnectPromise = null;
    }
  }

  async #disconnectHandle(handle) {
    if (handle.connectPromise) {
      try { await handle.connectPromise; } catch {}
    }

    handle.startupFailed = false;
    handle.targetStatus = "disconnected";
    if (!handle.adapter) {
      handle.disconnectFailed = false;
      const flushed = await this.#drainEvents(handle);
      if (!flushed) throw new Error(handle.lastError || "event_persistence_failed");
      handle.connectionEstablished = false;
      handle.sessions.clear();
      handle.pendingApprovals.clear();
      handle.approvalInFlight.clear();
      handle.status = "disconnected";
      return this.#snapshot(handle);
    }

    handle.status = "disconnecting";
    await this.#waitForCommands(handle);
    const adapter = handle.adapter;
    const unsubscribe = handle.unsubscribe;
    handle.disconnectFailed = false;

    let disconnectError = null;
    try {
      await adapter.disconnect();
    } catch (error) {
      disconnectError = error;
    }

    if (disconnectError) {
      handle.disconnectFailed = true;
      handle.targetStatus = "failed";
      const flushed = await this.#drainEvents(handle);
      if (!flushed) throw new Error(handle.lastError || "event_persistence_failed");
      handle.status = "failed";
      handle.lastError = safeErrorCode(disconnectError);
      throw new Error(handle.lastError);
    }

    if (handle.adapter === adapter) {
      handle.adapter = null;
      handle.connectionEstablished = false;
      handle.sessions.clear();
      handle.pendingApprovals.clear();
      handle.approvalInFlight.clear();
    }
    if (handle.unsubscribe === unsubscribe) {
      try { unsubscribe?.(); } catch {}
      handle.unsubscribe = null;
    }

    const flushed = await this.#drainEvents(handle);
    if (!flushed) throw new Error(handle.lastError || "event_persistence_failed");

    handle.disconnectFailed = false;
    handle.status = "disconnected";
    handle.lastError = null;
    return this.#snapshot(handle);
  }

  async retryEvents(agentId) {
    const handle = this.#requireHandle(agentId);
    const flushed = await this.#drainEvents(handle);
    if (!flushed) throw new Error(handle.lastError || "event_persistence_failed");
    return this.#snapshot(handle);
  }

  getAgentStatus(agentId) {
    const handle = this.#agents.get(agentId);
    return handle ? this.#snapshot(handle) : null;
  }

  listAgents() {
    return [...this.#agents.values()]
      .sort((a, b) => a.agentId.localeCompare(b.agentId))
      .map((handle) => this.#snapshot(handle));
  }

  subscribe(handler) {
    if (typeof handler !== "function") throw new TypeError("event_handler_required");
    this.#listeners.add(handler);
    return () => this.#listeners.delete(handler);
  }

  async shutdown() {
    this.#stopping = true;
    const failures = [];
    for (const handle of this.#agents.values()) {
      try { await this.disconnectAgent(handle.agentId); }
      catch { failures.push(handle.agentId); }
    }
    if (failures.length) throw new Error("runtime_shutdown_incomplete:" + failures.join(","));
    return this.listAgents();
  }

  async #withReadyAgent(agentId, operation) {
    const handle = this.#requireReady(agentId);
    handle.inFlightCommands += 1;
    try {
      return await operation(handle);
    } finally {
      handle.inFlightCommands -= 1;
      if (handle.inFlightCommands === 0) {
        for (const resolve of handle.commandWaiters.splice(0)) resolve();
      }
    }
  }

  #waitForCommands(handle) {
    if (handle.inFlightCommands === 0) return Promise.resolve();
    return new Promise((resolve) => handle.commandWaiters.push(resolve));
  }

  async #flushOrThrow(handle) {
    const flushed = await this.#drainEvents(handle);
    if (!flushed) throw new Error(handle.lastError || "event_persistence_failed");
  }

  #enqueueEvent(handle, input) {
    try {
      const event = createAgentEvent(input);
      if (event.agentId !== handle.agentId || event.providerId !== handle.descriptor?.providerId) {
        throw new Error("agent_event_identity_mismatch");
      }
      if (event.eventType === "approval.requested" &&
          (typeof event.payload.requestId !== "string" || !event.payload.requestId.trim())) {
        throw new Error("invalid_approval_event");
      }
      handle.eventQueue.push(event);
      void this.#drainEvents(handle);
    } catch {
      handle.fatalEventError = true;
      handle.status = "degraded";
      handle.lastError = "agent_event_invalid";
    }
  }

  #drainEvents(handle) {
    if (handle.drainPromise) return handle.drainPromise;
    handle.drainPromise = (async () => {
      while (handle.eventQueue.length) {
        const event = handle.eventQueue[0];
        let result;
        try {
          result = await this.#eventStore.append(event);
        } catch {
          handle.status = "degraded";
          handle.lastError = "event_persistence_failed";
          return false;
        }
        if (!result || typeof result.inserted !== "boolean") {
          handle.status = "degraded";
          handle.lastError = "invalid_event_store_result";
          return false;
        }
        handle.eventQueue.shift();
        if (event.eventType === "approval.requested" && handle.targetStatus !== "disconnected") {
          handle.pendingApprovals.set(event.payload.requestId, event);
        }
        if (event.eventType === "connection.changed" && event.payload.status === "disconnected") {
          handle.connectionEstablished = false;
          if (!handle.startupFailed && !handle.disconnectFailed) {
            handle.targetStatus = "disconnected";
            if (handle.status !== "disconnecting") handle.status = "disconnected";
          }
          handle.sessions.clear();
          handle.pendingApprovals.clear();
        }
        if (result.inserted) this.#publish(event);
      }

      if (handle.fatalEventError) {
        handle.status = "degraded";
        handle.lastError = handle.lastError || "agent_event_invalid";
        return false;
      }
      if (handle.status === "degraded") {
        handle.status = handle.targetStatus || (handle.adapter ? "ready" : "disconnected");
        handle.lastError = null;
      }
      return true;
    })().finally(() => {
      handle.drainPromise = null;
      if (handle.eventQueue.length && handle.status !== "degraded") {
        void this.#drainEvents(handle);
      }
    });
    return handle.drainPromise;
  }

  #publish(event) {
    for (const handler of this.#listeners) {
      try { handler(event); } catch {}
    }
  }

  #requireHandle(agentId) {
    agentId = requiredText(agentId, "agent_id");
    const handle = this.#agents.get(agentId);
    if (!handle) throw new Error("agent_not_found");
    return handle;
  }

  #requireReady(agentId) {
    const handle = this.#requireHandle(agentId);
    if (
      handle.status !== "ready" ||
      handle.disconnectPromise ||
      handle.eventQueue.length ||
      handle.fatalEventError
    ) {
      throw new Error("agent_not_ready");
    }
    return handle;
  }

  #snapshot(handle) {
    let health = null;
    try { health = handle.adapter?.getHealth?.() ?? null; } catch {}
    return Object.freeze({
      agentId: handle.agentId,
      adapterId: handle.adapterId,
      status: handle.status,
      connectionEstablished: handle.connectionEstablished,
      disconnectFailed: handle.disconnectFailed,
      health,
      sessionIds: Object.freeze([...handle.sessions].sort()),
      pendingApprovalCount: handle.pendingApprovals.size,
      pendingEventCount: handle.eventQueue.length,
      inFlightCommands: handle.inFlightCommands,
      lastError: handle.lastError,
    });
  }
}
