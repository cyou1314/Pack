import {validateHubURL} from "../hub-url-policy.mjs";

const MAX_RETRY_AFTER_MS = 24 * 60 * 60 * 1000;
const MAX_EVENT_BYTES = 256 * 1024;

function requiredText(value, field, maxLength = 160) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > maxLength) {
    throw new TypeError("invalid_" + field);
  }
  return value.trim();
}

/**
 * Negotiate additive Agent Event fields before a Runtime creates Hub-bound
 * Sessions. Unknown/old Hub versions fail closed and keep legacy event shape.
 */
export async function getAgentEventCapabilities({
  endpoint,
  token,
  fetchImpl = globalThis.fetch,
  timeoutMs = 15000,
} = {}) {
  const validatedEndpoint = validateHubURL(endpoint);
  const url = new URL(validatedEndpoint);
  if (!/\/agent\/events\/?$/.test(url.pathname)) {
    throw new TypeError("invalid_agent_event_endpoint");
  }
  if (typeof token !== "string" || !/^[\x21-\x7E]{1,256}$/.test(token)) {
    throw new TypeError("invalid_agent_event_token");
  }
  if (typeof fetchImpl !== "function") throw new TypeError("fetch_implementation_required");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 120000) {
    throw new TypeError("invalid_capability_timeout");
  }

  url.pathname = url.pathname.replace(/\/agent\/events\/?$/, "/agent/capabilities");
  let response;
  try {
    response = await fetchImpl(url.toString(), {
      method: "GET",
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
      headers: {authorization: "Bearer " + token},
    });
  } catch {
    return {supported: false, reason: "capability_unavailable"};
  }
  if (!response || !Number.isInteger(response.status)) {
    return {supported: false, reason: "invalid_capability_response"};
  }
  if (response.status === 404 || response.status === 405) {
    return {supported: false, reason: "capability_endpoint_unavailable"};
  }
  if (response.status === 401 || response.status === 403) {
    return {supported: false, reason: "authorization_required"};
  }
  if (response.status < 200 || response.status >= 300 || typeof response.text !== "function") {
    return {supported: false, reason: "capability_unavailable"};
  }

  let body;
  try {
    body = JSON.parse(await response.text());
  } catch {
    return {supported: false, reason: "invalid_capability_response"};
  }
  const supported = Number.isSafeInteger(body?.event_envelope_version)
    && body.event_envelope_version >= 2
    && Array.isArray(body?.supported_fields)
    && body.supported_fields.includes("hubSessionId")
    && Array.isArray(body?.features)
    && body.features.includes("hub-session-id-envelope-v1");
  return supported
    ? {
        supported: true,
        eventEnvelopeVersion: body.event_envelope_version,
        hubSessionIdField: true,
      }
    : {supported: false, reason: "hub_session_binding_unsupported"};
}

function parseRetryAfter(response, now) {
  const raw = response?.headers?.get?.("retry-after");
  if (typeof raw !== "string" || !raw.trim()) return null;

  const seconds = Number(raw);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.min(MAX_RETRY_AFTER_MS, seconds * 1000);
  }
  const timestamp = Date.parse(raw);
  if (!Number.isFinite(timestamp)) return null;
  return Math.min(MAX_RETRY_AFTER_MS, Math.max(0, timestamp - now));
}

/**
 * Delivers the oldest durable Outbox event to the Hub Event Gateway.
 *
 * This worker is intentionally caller-scheduled: runOnce() sends at most one
 * event, preserving Outbox order. The caller supplies a per-event credential
 * provider; bearer tokens are never stored or logged by this module.
 */
export class AgentEventDeliveryWorker {
  #eventStore;
  #endpoint;
  #getToken;
  #fetch;
  #clock;
  #baseRetryMs;
  #maxRetryMs;
  #timeoutMs;
  #paused = null;
  #running = null;

  constructor({
    eventStore,
    endpoint,
    getToken,
    fetchImpl = globalThis.fetch,
    clock = Date.now,
    baseRetryMs = 1000,
    maxRetryMs = 60000,
    timeoutMs = 15000,
  } = {}) {
    if (
      !eventStore ||
      typeof eventStore.listPending !== "function" ||
      typeof eventStore.getStats !== "function" ||
      typeof eventStore.getDeliveryState !== "function" ||
      typeof eventStore.scheduleRetry !== "function" ||
      typeof eventStore.markDelivered !== "function"
    ) {
      throw new TypeError("durable_event_store_required");
    }
    if (typeof getToken !== "function") throw new TypeError("event_token_provider_required");
    if (typeof fetchImpl !== "function") throw new TypeError("fetch_implementation_required");
    if (typeof clock !== "function") throw new TypeError("delivery_clock_required");
    if (!Number.isSafeInteger(baseRetryMs) || baseRetryMs < 100 || baseRetryMs > 60000) {
      throw new TypeError("invalid_base_retry_delay");
    }
    if (!Number.isSafeInteger(maxRetryMs) || maxRetryMs < baseRetryMs || maxRetryMs > 3600000) {
      throw new TypeError("invalid_max_retry_delay");
    }
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 120000) {
      throw new TypeError("invalid_delivery_timeout");
    }

    this.#eventStore = eventStore;
    this.#endpoint = validateHubURL(endpoint);
    if (!/\/agent\/events\/?$/.test(new URL(this.#endpoint).pathname)) {
      throw new TypeError("invalid_agent_event_endpoint");
    }
    this.#getToken = getToken;
    this.#fetch = fetchImpl;
    this.#clock = clock;
    this.#baseRetryMs = baseRetryMs;
    this.#maxRetryMs = maxRetryMs;
    this.#timeoutMs = timeoutMs;
  }

  getState() {
    return {paused: this.#paused !== null, reason: this.#paused};
  }

  resume() {
    this.#paused = null;
    return this.getState();
  }

  runOnce() {
    if (this.#paused) {
      return Promise.resolve({status: "paused", reason: this.#paused});
    }
    if (!this.#running) {
      this.#running = this.#runOnce().finally(() => {
        this.#running = null;
      });
    }
    return this.#running;
  }

  async #runOnce() {
    const now = this.#readClock();

    const events = await this.#eventStore.listPending({limit: 1, now});
    if (!events.length) {
      const stats = await this.#eventStore.getStats();
      return {status: stats.pending > 0 ? "waiting" : "idle"};
    }

    const event = events[0];
    const eventId = requiredText(event?.eventId, "event_id");
    let token;
    try {
      token = await this.#getToken(event);
    } catch {
      return this.#pause("credential_unavailable", eventId);
    }
    if (typeof token !== "string" || !/^[\x21-\x7E]{1,256}$/.test(token)) {
      return this.#pause("credential_unavailable", eventId);
    }

    let body;
    try {
      body = JSON.stringify(event);
    } catch {
      return this.#pause("invalid_local_event", eventId);
    }
    if (typeof body !== "string" || Buffer.byteLength(body, "utf8") > MAX_EVENT_BYTES) {
      return this.#pause("event_too_large", eventId);
    }

    let response;
    try {
      response = await this.#fetch(this.#endpoint, {
        method: "POST",
        redirect: "error",
        cache: "no-store",
        signal: AbortSignal.timeout(this.#timeoutMs),
        headers: {
          authorization: "Bearer " + token,
          "content-type": "application/json",
        },
        body,
      });
    } catch {
      return this.#scheduleRetry(eventId, "network_error", null, this.#readClock());
    }

    if (!response || !Number.isInteger(response.status) || response.status < 100 || response.status > 599) {
      return this.#pause("invalid_hub_response", eventId);
    }

    const status = response.status;
    if (status === 401 || status === 403) {
      return this.#pause("authorization_required", eventId, status);
    }
    if (status === 408 || status === 425 || status === 429 || (status >= 500 && status <= 599)) {
      const errorCode = status === 429
        ? "hub_rate_limited"
        : status >= 500
          ? "hub_unavailable"
          : "hub_timeout";
      return this.#scheduleRetry(eventId, errorCode, response, this.#readClock());
    }
    if (status < 200 || status >= 300) {
      return this.#pause("hub_rejected_event", eventId, status);
    }

    if (typeof response.text !== "function") {
      return this.#pause("invalid_hub_response", eventId, status);
    }

    let receiptText;
    try {
      receiptText = await response.text();
    } catch {
      // Fetch resolves when headers arrive. A socket close or timeout while
      // reading the body is still a transient transport failure, not a bad
      // Hub receipt; retry the same eventId so Hub Inbox deduplication applies.
      return this.#scheduleRetry(
        eventId,
        "hub_response_body_error",
        null,
        this.#readClock(),
      );
    }

    if (typeof receiptText !== "string") {
      return this.#pause("invalid_hub_receipt", eventId, status);
    }

    let receipt;
    try {
      receipt = JSON.parse(receiptText);
    } catch {
      return this.#pause("invalid_hub_receipt", eventId, status);
    }
    if (
      !receipt ||
      receipt.accepted !== true ||
      typeof receipt.duplicate !== "boolean" ||
      !Number.isSafeInteger(receipt.inboxSeq) ||
      receipt.inboxSeq < 1
    ) {
      return this.#pause("invalid_hub_receipt", eventId, status);
    }

    await this.#eventStore.markDelivered([eventId]);
    return {
      status: "delivered",
      eventId,
      inboxSeq: receipt.inboxSeq,
      duplicate: receipt.duplicate,
    };
  }

  #readClock() {
    const now = this.#clock();
    if (!Number.isFinite(now) || now < 0) throw new TypeError("invalid_delivery_clock");
    return now;
  }

  async #scheduleRetry(eventId, errorCode, response, failedAt) {
    const state = await this.#eventStore.getDeliveryState(eventId);
    const attemptCount = (state?.attemptCount ?? 0) + 1;
    const exponentialDelay = Math.min(
      this.#maxRetryMs,
      this.#baseRetryMs * (2 ** Math.min(attemptCount - 1, 16)),
    );
    const retryAfter = parseRetryAfter(response, failedAt);
    const delayMs = retryAfter === null ? exponentialDelay : retryAfter;
    const scheduled = await this.#eventStore.scheduleRetry(eventId, {
      now: failedAt,
      delayMs,
      errorCode,
    });
    return {
      status: "retry_scheduled",
      eventId,
      attemptCount: scheduled.attemptCount,
      nextAttemptAt: scheduled.nextAttemptAt,
      errorCode,
    };
  }

  #pause(reason, eventId, httpStatus = null) {
    this.#paused = reason;
    return {status: "paused", reason, eventId, httpStatus};
  }
}
