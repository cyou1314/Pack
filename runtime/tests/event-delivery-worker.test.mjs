import test from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync, rmSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {SqliteEventStore} from "../agent-core/sqlite-event-store.mjs";
import {AgentEventDeliveryWorker} from "../agent-core/event-delivery-worker.mjs";

const ENDPOINT = "http://127.0.0.1:8794/agent/events";

function makeEvent(eventId, sequence = 1) {
  return {
    eventId,
    eventType: "run.progress",
    providerId: "codex",
    agentId: "agent-1",
    sequence,
    occurredAt: "2026-11-24T03:00:00.000Z",
    payload: {progress: sequence},
  };
}

function fakeResponse(status, body, headers = {}) {
  return {
    status,
    headers: {
      get(name) {
        return headers[name.toLowerCase()] ?? null;
      },
    },
    async text() {
      if (body instanceof Error) throw body;
      return typeof body === "string" ? body : JSON.stringify(body);
    },
  };
}

function receipt(inboxSeq = 1, duplicate = false) {
  return {accepted: true, duplicate, inboxSeq};
}

async function withDatabase(run) {
  const directory = mkdtempSync(join(tmpdir(), "pack-event-delivery-"));
  const databasePath = join(directory, "runtime", "events.sqlite");
  try {
    return await run(databasePath);
  } finally {
    rmSync(directory, {recursive: true, force: true});
  }
}

function makeWorker(eventStore, options = {}) {
  return new AgentEventDeliveryWorker({
    eventStore,
    endpoint: options.endpoint ?? ENDPOINT,
    getToken: options.getToken ?? (async () => "agent-event-secret"),
    fetchImpl: options.fetchImpl ?? (async () => fakeResponse(200, receipt())),
    clock: options.clock ?? (() => 1000),
    baseRetryMs: options.baseRetryMs ?? 1000,
    maxRetryMs: options.maxRetryMs ?? 8000,
  });
}

test("delivery worker marks an event delivered only after a valid Hub receipt", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    const event = makeEvent("event-1");
    await store.append(event);
    let request;
    const worker = makeWorker(store, {
      fetchImpl: async (url, options) => {
        request = {url, options};
        return fakeResponse(200, receipt(7, false));
      },
    });

    assert.deepEqual(await worker.runOnce(), {
      status: "delivered",
      eventId: "event-1",
      inboxSeq: 7,
      duplicate: false,
    });
    assert.equal(request.url, ENDPOINT);
    assert.equal(request.options.method, "POST");
    assert.equal(request.options.redirect, "error");
    assert.ok(request.options.signal instanceof AbortSignal);
    assert.equal(request.options.headers.authorization, "Bearer agent-event-secret");
    assert.deepEqual(JSON.parse(request.options.body), event);
    assert.deepEqual(await store.listPending(), []);
    store.close();
  });
});

test("duplicate Hub receipt safely completes an Outbox replay", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append(makeEvent("event-duplicate"));
    const worker = makeWorker(store, {
      fetchImpl: async () => fakeResponse(200, receipt(9, true)),
    });
    assert.deepEqual(await worker.runOnce(), {
      status: "delivered",
      eventId: "event-duplicate",
      inboxSeq: 9,
      duplicate: true,
    });
    assert.deepEqual(await store.listPending(), []);
    store.close();
  });
});

test("retryable failures persist exponential backoff and preserve Outbox order across reopen", async () => {
  await withDatabase(async (databasePath) => {
    let now = 1000;
    let store = new SqliteEventStore({databasePath});
    const first = makeEvent("event-1", 1);
    const second = makeEvent("event-2", 2);
    await store.append(first);
    await store.append(second);
    const sent = [];
    const fetchImpl = async (_url, options) => {
      const event = JSON.parse(options.body);
      sent.push(event.eventId);
      if (sent.length === 1) return fakeResponse(503, {error: "unavailable"});
      return fakeResponse(200, receipt(sent.length, false));
    };
    let worker = makeWorker(store, {fetchImpl, clock: () => now});

    assert.deepEqual(await worker.runOnce(), {
      status: "retry_scheduled",
      eventId: "event-1",
      attemptCount: 1,
      nextAttemptAt: 2000,
      errorCode: "hub_unavailable",
    });
    assert.deepEqual(await store.listPending({now: 1999}), []);
    store.close();

    store = new SqliteEventStore({databasePath});
    worker = makeWorker(store, {fetchImpl, clock: () => now});
    assert.deepEqual(await worker.runOnce(), {status: "waiting"});
    assert.deepEqual(sent, ["event-1"]);

    now = 2000;
    assert.equal((await worker.runOnce()).eventId, "event-1");
    now = 2001;
    assert.equal((await worker.runOnce()).eventId, "event-2");
    assert.deepEqual(sent, ["event-1", "event-1", "event-2"]);
    assert.deepEqual(await store.listPending(), []);
    store.close();
  });
});

test("Retry-After is persisted for rate-limited delivery", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append(makeEvent("event-429"));
    const worker = makeWorker(store, {
      fetchImpl: async () => fakeResponse(429, {}, {"retry-after": "5"}),
      clock: () => 1000,
    });
    const result = await worker.runOnce();
    assert.equal(result.status, "retry_scheduled");
    assert.equal(result.nextAttemptAt, 6000);
    assert.deepEqual(await store.listPending({now: 5999}), []);
    assert.equal((await store.getDeliveryState("event-429")).lastErrorCode, "hub_rate_limited");
    store.close();
  });
});

test("authorization failure pauses without discarding the event and can resume", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append(makeEvent("event-auth"));
    let authorized = false;
    let calls = 0;
    const worker = makeWorker(store, {
      getToken: async () => authorized ? "replacement-token" : "expired-token",
      fetchImpl: async () => {
        calls += 1;
        return authorized
          ? fakeResponse(200, receipt(11, false))
          : fakeResponse(401, {error: "unauthorized"});
      },
    });

    assert.equal((await worker.runOnce()).status, "paused");
    assert.deepEqual(worker.getState(), {paused: true, reason: "authorization_required"});
    assert.equal((await worker.runOnce()).status, "paused");
    assert.equal(calls, 1);
    assert.equal((await store.getStats()).pending, 1);

    authorized = true;
    assert.deepEqual(worker.resume(), {paused: false, reason: null});
    assert.equal((await worker.runOnce()).status, "delivered");
    assert.equal(calls, 2);
    assert.deepEqual(await store.listPending(), []);
    store.close();
  });
});

test("invalid bearer token values are rejected before making a request", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append(makeEvent("event-bad-token"));
    let calls = 0;
    const worker = makeWorker(store, {
      getToken: async () => "secret\r\nX-Injected: true",
      fetchImpl: async () => {
        calls += 1;
        return fakeResponse(200, receipt());
      },
    });
    assert.equal((await worker.runOnce()).status, "paused");
    assert.deepEqual(worker.getState(), {paused: true, reason: "credential_unavailable"});
    assert.equal(calls, 0);
    store.close();
  });
});

test("invalid Hub receipt pauses and leaves the Outbox event pending", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append(makeEvent("event-invalid-receipt"));
    const worker = makeWorker(store, {
      fetchImpl: async () => fakeResponse(200, {accepted: false, duplicate: false, inboxSeq: 1}),
    });
    assert.equal((await worker.runOnce()).status, "paused");
    assert.deepEqual(worker.getState(), {paused: true, reason: "invalid_hub_receipt"});
    assert.equal((await store.getStats()).pending, 1);
    store.close();
  });
});

test("oversized local events are paused before sending", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append({
      ...makeEvent("event-too-large"),
      payload: {text: "x".repeat(256 * 1024)},
    });
    let calls = 0;
    const worker = makeWorker(store, {
      fetchImpl: async () => {
        calls += 1;
        return fakeResponse(200, receipt());
      },
    });
    assert.deepEqual(await worker.runOnce(), {
      status: "paused",
      reason: "event_too_large",
      eventId: "event-too-large",
      httpStatus: null,
    });
    assert.equal(calls, 0);
    assert.equal((await store.getStats()).pending, 1);
    store.close();
  });
});

test("network failures schedule durable retry", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append(makeEvent("event-network"));
    const worker = makeWorker(store, {
      fetchImpl: async () => { throw new Error("network unavailable"); },
      clock: () => 1000,
    });
    const result = await worker.runOnce();
    assert.equal(result.status, "retry_scheduled");
    assert.equal(result.errorCode, "network_error");
    assert.equal(result.nextAttemptAt, 2000);
    store.close();
  });
});

test("a response body transport failure after 2xx headers schedules a durable retry", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append(makeEvent("event-body-drop"));
    const worker = makeWorker(store, {
      fetchImpl: async () => fakeResponse(200, new TypeError("response body terminated")),
      clock: () => 1000,
    });

    const result = await worker.runOnce();
    assert.equal(result.status, "retry_scheduled");
    assert.equal(result.errorCode, "hub_response_body_error");
    assert.equal(result.nextAttemptAt, 2000);
    assert.equal((await store.getStats()).pending, 1);
    assert.equal((await store.getDeliveryState("event-body-drop")).attemptCount, 1);
    store.close();
  });
});

test("malformed receipt JSON pauses instead of treating the body as a transport failure", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append(makeEvent("event-malformed-receipt"));
    const worker = makeWorker(store, {
      fetchImpl: async () => fakeResponse(200, "{not-json"),
      clock: () => 1000,
    });

    assert.deepEqual(await worker.runOnce(), {
      status: "paused",
      reason: "invalid_hub_receipt",
      eventId: "event-malformed-receipt",
      httpStatus: 200,
    });
    assert.equal((await store.getStats()).pending, 1);
    assert.equal((await store.getDeliveryState("event-malformed-receipt")).attemptCount, 0);
    store.close();
  });
});

test("retry backoff starts when the HTTP failure occurs, not when the request starts", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append(makeEvent("event-slow-503"));
    let now = 1000;
    const worker = makeWorker(store, {
      fetchImpl: async () => {
        now = 16000;
        return fakeResponse(503, {error: "unavailable"});
      },
      clock: () => now,
      baseRetryMs: 1000,
      maxRetryMs: 8000,
    });

    const result = await worker.runOnce();
    assert.equal(result.status, "retry_scheduled");
    assert.equal(result.nextAttemptAt, 17000);
    assert.deepEqual(await store.listPending({now: 16999}), []);
    assert.equal((await store.getDeliveryState("event-slow-503")).nextAttemptAt, 17000);
    store.close();
  });
});

test("Retry-After delta seconds start when a delayed 429 response arrives", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append(makeEvent("event-slow-429"));
    let now = 1000;
    const worker = makeWorker(store, {
      fetchImpl: async () => {
        now = 16000;
        return fakeResponse(429, {error: "rate limited"}, {"retry-after": "5"});
      },
      clock: () => now,
    });

    const result = await worker.runOnce();
    assert.equal(result.status, "retry_scheduled");
    assert.equal(result.nextAttemptAt, 21000);
    assert.deepEqual(await store.listPending({now: 20999}), []);
    store.close();
  });
});

test("delivery worker requires HTTPS for remote Hub endpoints", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    assert.throws(
      () => makeWorker(store, {endpoint: "http://hub.example.com/agent/events"}),
      /invalid_hub_url/,
    );
    assert.doesNotThrow(() => makeWorker(store, {endpoint: ENDPOINT}));
    assert.throws(
      () => makeWorker(store, {endpoint: "http://127.0.0.1:8794/mcp"}),
      /invalid_agent_event_endpoint/,
    );
    store.close();
  });
});

test("concurrent runOnce calls share one in-flight delivery", async () => {
  await withDatabase(async (databasePath) => {
    const store = new SqliteEventStore({databasePath});
    await store.append(makeEvent("event-single-flight"));
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    let calls = 0;
    const worker = makeWorker(store, {
      fetchImpl: async () => {
        calls += 1;
        await gate;
        return fakeResponse(200, receipt(13, false));
      },
    });

    const first = worker.runOnce();
    const second = worker.runOnce();
    assert.strictEqual(first, second);
    release();
    await Promise.all([first, second]);
    assert.equal(calls, 1);
    assert.deepEqual(await store.listPending(), []);
    store.close();
  });
});
