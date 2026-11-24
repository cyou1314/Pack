import {createHash, randomUUID} from "node:crypto";
import {
  chmod,
  link,
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
  stat as statPath,
  unlink,
} from "node:fs/promises";
import path from "node:path";
import {createAgentEvent} from "./contract.mjs";

const FORMAT_VERSION = 1;
const DEFAULT_MAX_EVENT_BYTES = 4 * 1024 * 1024;
const DEFAULT_PAGE_SIZE = 100;
const MAX_PAGE_SIZE = 1000;
const MAX_EVENT_DEPTH = 64;
const HASH_PATTERN = /^[a-f0-9]{64}$/;
const STORE_MARKER = ".pack-event-store";
const STORE_MARKER_CONTENT = "pack-runtime-event-store-v1\n";

function requiredText(value, field, maxLength = 160) {
  if (typeof value !== "string" || !value.trim() || value.trim().length > maxLength) {
    throw new TypeError("invalid_" + field);
  }
  return value.trim();
}

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

function eventIdDigest(value) {
  return digest(JSON.stringify(value));
}

function isPlainObject(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasSerializationHook(value) {
  for (let current = value; current; current = Object.getPrototypeOf(current)) {
    if (Object.getOwnPropertyDescriptor(current, "toJSON")) return true;
  }
  return false;
}

function cloneJsonValue(value, seen = new Set(), depth = 0) {
  if (depth > MAX_EVENT_DEPTH) throw new TypeError("event_payload_too_deep");
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "object" || seen.has(value) || hasSerializationHook(value)) {
    throw new TypeError("event_not_json_serializable");
  }

  seen.add(value);
  let result;
  if (Array.isArray(value)) {
    const prototype = Object.getPrototypeOf(value);
    if ((prototype !== Array.prototype && prototype !== null) ||
        Object.keys(value).length !== value.length ||
        Object.getOwnPropertySymbols(value).length) {
      throw new TypeError("event_not_json_serializable");
    }
    result = [];
    Object.setPrototypeOf(result, null);
    for (let index = 0; index < value.length; index += 1) {
      const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
      if (!descriptor || !descriptor.enumerable || !Object.hasOwn(descriptor, "value")) {
        throw new TypeError("event_not_json_serializable");
      }
      result[index] = cloneJsonValue(descriptor.value, seen, depth + 1);
    }
  } else {
    if (!isPlainObject(value) || Object.getOwnPropertySymbols(value).length) {
      throw new TypeError("event_not_json_serializable");
    }
    result = Object.create(null);
    for (const key of Object.keys(value)) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor || !descriptor.enumerable || !Object.hasOwn(descriptor, "value")) {
        throw new TypeError("event_not_json_serializable");
      }
      result[key] = cloneJsonValue(descriptor.value, seen, depth + 1);
    }
  }
  seen.delete(value);
  return result;
}


function canonicalJson(value) {
  if (Array.isArray(value)) {
    const parts = [];
    for (let index = 0; index < value.length; index += 1) parts.push(canonicalJson(value[index]));
    return "[" + parts.join(",") + "]";
  }
  if (isPlainObject(value)) {
    return "{" + Object.keys(value).sort().map((key) =>
      JSON.stringify(key) + ":" + canonicalJson(value[key])
    ).join(",") + "}";
  }
  return JSON.stringify(value);
}


function normalizeEvent(input, maxEventBytes) {
  if (!isPlainObject(input)) return createAgentEvent(input);
  if (Object.getOwnPropertySymbols(input).length) {
    throw new TypeError("event_not_json_serializable");
  }
  if (Object.getOwnPropertyDescriptor(input, "toJSON")) {
    throw new TypeError("event_not_json_serializable");
  }

  const safeInput = Object.create(null);
  for (const key of Object.keys(input)) {
    const descriptor = Object.getOwnPropertyDescriptor(input, key);
    if (!descriptor || !descriptor.enumerable || !Object.hasOwn(descriptor, "value")) {
      throw new TypeError("event_not_json_serializable");
    }
    safeInput[key] = descriptor.value;
  }
  const payloadDescriptor = Object.getOwnPropertyDescriptor(input, "payload");
  if (payloadDescriptor && (!payloadDescriptor.enumerable || !Object.hasOwn(payloadDescriptor, "value"))) {
    throw new TypeError("event_not_json_serializable");
  }
  if (payloadDescriptor?.value !== undefined) {
    safeInput.payload = cloneJsonValue(payloadDescriptor.value);
  }

  const event = createAgentEvent(safeInput);
  const safeEvent = cloneJsonValue(event);
  const serialized = JSON.stringify(safeEvent);
  if (Buffer.byteLength(serialized, "utf8") > maxEventBytes) {
    throw new TypeError("event_too_large");
  }
  const persistedEvent = JSON.parse(serialized);
  return {
    event: persistedEvent,
    eventHash: digest(canonicalJson(persistedEvent)),
  };
}


function storeError(error) {
  if (error instanceof Error && /^[a-z0-9_.:-]{1,80}$/i.test(error.message)) return error;
  return new Error("event_store_io_failed");
}

function isMissing(error) {
  return error?.code === "ENOENT";
}

async function initializeStoreDirectory(directory) {
  if (directory === path.parse(directory).root) throw new Error("event_store_unsafe_path");
  const parentDirectory = path.dirname(directory);
  let parentStat;
  try {
    parentStat = await statPath(parentDirectory);
  } catch (error) {
    if (isMissing(error)) throw new Error("event_store_parent_missing");
    throw error;
  }
  if (!parentStat.isDirectory()) throw new Error("event_store_unsafe_path");

  try {
    await mkdir(directory, {mode: 0o700});
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
  }
  const stat = await lstat(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new Error("event_store_unsafe_path");
  }

  const markerPath = path.join(directory, STORE_MARKER);
  let markerExists = true;
  try {
    await lstat(markerPath);
  } catch (error) {
    if (!isMissing(error)) throw error;
    markerExists = false;
  }

  if (!markerExists) {
    if ((await readdir(directory)).length) throw new Error("event_store_unsafe_path");
    let handle;
    try {
      handle = await open(markerPath, "wx", 0o600);
      await handle.writeFile(STORE_MARKER_CONTENT, "utf8");
      await handle.sync();
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
    } finally {
      await handle?.close().catch(() => {});
    }
  }

  const markerStat = await lstat(markerPath);
  if (!markerStat.isFile() || markerStat.isSymbolicLink()) {
    throw new Error("event_store_unsafe_path");
  }
  await chmod(markerPath, 0o600);
  if ((await readFile(markerPath, "utf8")) !== STORE_MARKER_CONTENT) {
    throw new Error("event_store_corrupt");
  }
  await chmod(directory, 0o700);
  await syncFile(markerPath);
  await syncDirectory(directory);
  await syncDirectory(parentDirectory);
}

async function ensurePrivateDirectory(directory) {
  try {
    await mkdir(directory, {mode: 0o700});
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
  }
  const stat = await lstat(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new Error("event_store_unsafe_path");
  }
  await chmod(directory, 0o700);
}

async function syncFile(filePath) {
  const handle = await open(filePath, "r");
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function syncDirectory(directory) {
  const handle = await open(directory, "r");
  try {
    await handle.sync();
  } finally {
    await handle.close();
  }
}

async function readJsonRecord(filePath, kind, expectedHash, maxBytes) {
  let stat;
  try {
    stat = await lstat(filePath);
  } catch (error) {
    if (isMissing(error)) return null;
    throw error;
  }
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error("event_store_corrupt");
  if (stat.size > maxBytes + 8192) throw new Error("event_store_corrupt");
  await chmod(filePath, 0o600);

  let record;
  try {
    const source = await readFile(filePath, "utf8");
    if (!source.endsWith("\n")) throw new Error("event_store_corrupt");
    record = JSON.parse(source);
  } catch (error) {
    if (error?.message === "event_store_corrupt") throw error;
    throw new Error("event_store_corrupt");
  }

  if (!isPlainObject(record) ||
      record.version !== FORMAT_VERSION ||
      record.kind !== kind ||
      typeof record.eventId !== "string" ||
      eventIdDigest(record.eventId) !== expectedHash ||
      !Number.isSafeInteger(record.order) ||
      record.order < 1 ||
      typeof record.eventHash !== "string" ||
      !HASH_PATTERN.test(record.eventHash)) {
    throw new Error("event_store_corrupt");
  }

  if (kind === "event") {
    let event;
    try {
      const safePayload = cloneJsonValue(record.event?.payload ?? {});
      event = createAgentEvent({...record.event, payload: safePayload});
      const safeEvent = cloneJsonValue(event);
      if (event.eventId !== record.eventId ||
          canonicalJson(safeEvent) !== canonicalJson(record.event) ||
          digest(canonicalJson(safeEvent)) !== record.eventHash) {
        throw new Error("event_store_corrupt");
      }
    } catch {
      throw new Error("event_store_corrupt");
    }
    return {kind, eventId: record.eventId, eventHash: record.eventHash, order: record.order, event: record.event};
  }

  if (typeof record.acknowledgedAt !== "string" ||
      !Number.isFinite(Date.parse(record.acknowledgedAt))) {
    throw new Error("event_store_corrupt");
  }
  return {
    kind,
    eventId: record.eventId,
    eventHash: record.eventHash,
    order: record.order,
    acknowledgedAt: record.acknowledgedAt,
  };
}


async function atomicCreateJson(directory, target, record) {
  const temporary = path.join(directory, ".tmp-" + randomUUID());
  let handle;
  let createdTemporary = false;
  try {
    handle = await open(temporary, "wx", 0o600);
    createdTemporary = true;
    await handle.writeFile(JSON.stringify(cloneJsonValue(record)) + "\n", "utf8");
    await handle.sync();
    await handle.close();
    handle = null;

    try {
      await link(temporary, target);
      return true;
    } catch (error) {
      if (error?.code === "EEXIST") return false;
      throw error;
    }
  } finally {
    await handle?.close().catch(() => {});
    if (createdTemporary) await unlink(temporary).catch(() => {});
  }
}


async function cleanupTemporaryFiles(directory) {
  let changed = false;
  for (const entry of await readdir(directory, {withFileTypes: true})) {
    if (!entry.name.startsWith(".tmp-")) continue;
    const filePath = path.join(directory, entry.name);
    const stat = await lstat(filePath);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error("event_store_corrupt");
    await unlink(filePath);
    changed = true;
  }
  if (changed) await syncDirectory(directory);
}

function deepFreeze(value, seen = new Set()) {
  if (!value || typeof value !== "object" || seen.has(value)) return value;
  seen.add(value);
  for (const child of Object.values(value)) deepFreeze(child, seen);
  return Object.freeze(value);
}

/**
 * A dependency-free, crash-recoverable local event outbox.
 *
 * Each event is committed as an fsynced file in pending/. Acknowledgements
 * are separate durable tombstones in acknowledged/, so a repeated eventId
 * remains deduplicated after its payload is removed. The store is designed
 * for one Runtime process per directory; file creation and acknowledgement
 * are atomic across concurrent callers.
 */
export class DurableEventStore {
  #directory;
  #pendingDirectory;
  #acknowledgedDirectory;
  #maxEventBytes;
  #lastOrder = 0;
  #tail = Promise.resolve();
  #closed = false;

  static async open({directory, maxEventBytes = DEFAULT_MAX_EVENT_BYTES} = {}) {
    directory = requiredText(directory, "event_store_directory", 4096);
    if (!Number.isSafeInteger(maxEventBytes) || maxEventBytes < 1024 || maxEventBytes > 64 * 1024 * 1024) {
      throw new TypeError("invalid_event_store_max_bytes");
    }

    const store = new DurableEventStore(path.resolve(directory), maxEventBytes);
    await store.#initialize();
    return store;
  }

  constructor(directory, maxEventBytes) {
    this.#directory = directory;
    this.#pendingDirectory = path.join(directory, "pending");
    this.#acknowledgedDirectory = path.join(directory, "acknowledged");
    this.#maxEventBytes = maxEventBytes;
  }

  async #initialize() {
    try {
      await initializeStoreDirectory(this.#directory);
      await ensurePrivateDirectory(this.#pendingDirectory);
      await ensurePrivateDirectory(this.#acknowledgedDirectory);
      await syncDirectory(this.#directory);
      await cleanupTemporaryFiles(this.#pendingDirectory);
      await cleanupTemporaryFiles(this.#acknowledgedDirectory);

      const acknowledged = await this.#scanDirectory(this.#acknowledgedDirectory, "ack");
      const pending = await this.#scanDirectory(this.#pendingDirectory, "event");
      if (acknowledged.size) await syncDirectory(this.#acknowledgedDirectory);
      if (pending.size) await syncDirectory(this.#pendingDirectory);
      let removedPending = false;

      for (const record of acknowledged.values()) {
        this.#lastOrder = Math.max(this.#lastOrder, record.order);
      }
      for (const [hash, record] of pending) {
        this.#lastOrder = Math.max(this.#lastOrder, record.order);
        const ack = acknowledged.get(hash);
        if (!ack) continue;
        if (ack.eventId !== record.eventId ||
            ack.eventHash !== record.eventHash ||
            ack.order !== record.order) {
          throw new Error("event_store_corrupt");
        }
        await unlink(path.join(this.#pendingDirectory, hash + ".json"));
        removedPending = true;
      }
      if (removedPending) await syncDirectory(this.#pendingDirectory);
    } catch (error) {
      throw storeError(error);
    }
  }

  async #scanDirectory(directory, kind) {
    const records = new Map();
    for (const entry of await readdir(directory, {withFileTypes: true})) {
      if (!entry.isFile() || !entry.name.endsWith(".json")) {
        throw new Error("event_store_corrupt");
      }
      const hash = entry.name.slice(0, -5);
      if (!HASH_PATTERN.test(hash)) throw new Error("event_store_corrupt");
      const record = await readJsonRecord(
        path.join(directory, entry.name),
        kind,
        hash,
        this.#maxEventBytes,
      );
      if (!record || records.has(hash)) throw new Error("event_store_corrupt");
      records.set(hash, record);
    }
    return records;
  }

  #enqueue(operation) {
    const result = this.#tail.then(async () => {
      if (this.#closed) throw new Error("event_store_closed");
      return operation();
    });
    this.#tail = result.catch(() => {});
    return result;
  }

  async append(input) {
    return this.#enqueue(async () => {
      try {
        const {event, eventHash} = normalizeEvent(input, this.#maxEventBytes);
        const hash = eventIdDigest(event.eventId);
        const pendingPath = path.join(this.#pendingDirectory, hash + ".json");
        const acknowledgedPath = path.join(this.#acknowledgedDirectory, hash + ".json");

        const acknowledged = await readJsonRecord(
          acknowledgedPath, "ack", hash, this.#maxEventBytes,
        );
        if (acknowledged) {
          if (acknowledged.eventHash !== eventHash) throw new Error("event_id_conflict");
          await syncDirectory(this.#acknowledgedDirectory);
          this.#lastOrder = Math.max(this.#lastOrder, acknowledged.order);
          return {inserted: false, order: acknowledged.order};
        }

        const pending = await readJsonRecord(
          pendingPath, "event", hash, this.#maxEventBytes,
        );
        if (pending) {
          if (pending.eventHash !== eventHash) throw new Error("event_id_conflict");
          await syncDirectory(this.#pendingDirectory);
          this.#lastOrder = Math.max(this.#lastOrder, pending.order);
          return {inserted: false, order: pending.order};
        }

        const order = this.#lastOrder + 1;
        if (!Number.isSafeInteger(order)) throw new Error("event_store_order_exhausted");
        const record = {
          version: FORMAT_VERSION,
          kind: "event",
          eventId: event.eventId,
          eventHash,
          order,
          event,
        };
        const created = await atomicCreateJson(this.#pendingDirectory, pendingPath, record);
        if (!created) {
          const existingAck = await readJsonRecord(
            acknowledgedPath, "ack", hash, this.#maxEventBytes,
          );
          const existingPending = existingAck ? null : await readJsonRecord(
            pendingPath, "event", hash, this.#maxEventBytes,
          );
          const existing = existingAck || existingPending;
          if (!existing) throw new Error("event_store_write_conflict");
          if (existing.eventHash !== eventHash) throw new Error("event_id_conflict");
          await syncDirectory(existingAck ? this.#acknowledgedDirectory : this.#pendingDirectory);
          this.#lastOrder = Math.max(this.#lastOrder, existing.order);
          return {inserted: false, order: existing.order};
        }

        this.#lastOrder = Math.max(this.#lastOrder, order);
        await syncDirectory(this.#pendingDirectory);

        const racedAck = await readJsonRecord(
          acknowledgedPath, "ack", hash, this.#maxEventBytes,
        );
        if (racedAck) {
          if (racedAck.eventHash !== eventHash || racedAck.order !== order) {
            throw new Error("event_id_conflict");
          }
          await unlink(pendingPath).catch((error) => { if (!isMissing(error)) throw error; });
          await syncDirectory(this.#pendingDirectory);
          return {inserted: false, order};
        }
        return {inserted: true, order};
      } catch (error) {
        throw storeError(error);
      }
    });
  }

  async listPending({limit = DEFAULT_PAGE_SIZE} = {}) {
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_PAGE_SIZE) {
      throw new TypeError("invalid_event_store_limit");
    }
    return this.#enqueue(async () => {
      try {
        await cleanupTemporaryFiles(this.#pendingDirectory);
        const pending = await this.#scanDirectory(this.#pendingDirectory, "event");
        if (pending.size) await syncDirectory(this.#pendingDirectory);
        const results = [];
        let removedPending = false;

        for (const [hash, record] of pending) {
          const acknowledged = await readJsonRecord(
            path.join(this.#acknowledgedDirectory, hash + ".json"),
            "ack",
            hash,
            this.#maxEventBytes,
          );
          if (acknowledged) {
            if (acknowledged.eventId !== record.eventId ||
                acknowledged.eventHash !== record.eventHash ||
                acknowledged.order !== record.order) {
              throw new Error("event_store_corrupt");
            }
            await syncDirectory(this.#acknowledgedDirectory);
            await unlink(path.join(this.#pendingDirectory, hash + ".json"));
            removedPending = true;
            continue;
          }
          results.push({order: record.order, event: deepFreeze(record.event)});
        }

        if (removedPending) await syncDirectory(this.#pendingDirectory);
        results.sort((left, right) =>
          left.order - right.order || left.event.eventId.localeCompare(right.event.eventId)
        );
        return results.slice(0, limit);
      } catch (error) {
        throw storeError(error);
      }
    });
  }

  async acknowledge(eventId) {
    eventId = requiredText(eventId, "event_id");
    return this.#enqueue(async () => {
      try {
        const hash = eventIdDigest(eventId);
        const pendingPath = path.join(this.#pendingDirectory, hash + ".json");
        const acknowledgedPath = path.join(this.#acknowledgedDirectory, hash + ".json");
        const existingAck = await readJsonRecord(
          acknowledgedPath, "ack", hash, this.#maxEventBytes,
        );
        if (existingAck) {
          const pending = await readJsonRecord(
            pendingPath, "event", hash, this.#maxEventBytes,
          );
          if (pending && (pending.eventHash !== existingAck.eventHash ||
              pending.order !== existingAck.order)) {
            throw new Error("event_id_conflict");
          }
          await syncDirectory(this.#acknowledgedDirectory);
          await unlink(pendingPath).catch((error) => { if (!isMissing(error)) throw error; });
          await syncDirectory(this.#pendingDirectory);
          return {acknowledged: false};
        }

        const pending = await readJsonRecord(
          pendingPath, "event", hash, this.#maxEventBytes,
        );
        if (!pending) return {acknowledged: false};

        const record = {
          version: FORMAT_VERSION,
          kind: "ack",
          eventId,
          eventHash: pending.eventHash,
          order: pending.order,
          acknowledgedAt: new Date().toISOString(),
        };
        const created = await atomicCreateJson(
          this.#acknowledgedDirectory, acknowledgedPath, record,
        );
        const ack = created ? record : await readJsonRecord(
          acknowledgedPath, "ack", hash, this.#maxEventBytes,
        );
        if (!ack || ack.eventHash !== pending.eventHash || ack.order !== pending.order) {
          throw new Error("event_id_conflict");
        }
        await syncDirectory(this.#acknowledgedDirectory);
        await unlink(pendingPath).catch((error) => { if (!isMissing(error)) throw error; });
        await syncDirectory(this.#pendingDirectory);
        return {acknowledged: created};
      } catch (error) {
        throw storeError(error);
      }
    });
  }

  async close() {
    if (this.#closed) return;
    const closing = this.#tail.then(() => { this.#closed = true; });
    this.#tail = closing.catch(() => {});
    await closing;
  }
}
