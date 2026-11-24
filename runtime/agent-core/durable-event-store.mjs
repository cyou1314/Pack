import {createHash, randomUUID} from "node:crypto";
import {
  chmod,
  link,
  lstat,
  mkdir,
  open,
  readFile,
  readdir,
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

function isPlainObject(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function assertJsonValue(value, seen = new Set(), depth = 0) {
  if (depth > MAX_EVENT_DEPTH) throw new TypeError("event_payload_too_deep");
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number" && Number.isFinite(value)) return;
  if (typeof value !== "object" || seen.has(value)) {
    throw new TypeError("event_not_json_serializable");
  }

  seen.add(value);
  if (Array.isArray(value)) {
    if (Object.getPrototypeOf(value) !== Array.prototype ||
        Object.keys(value).length !== value.length ||
        Object.getOwnPropertySymbols(value).length) {
      throw new TypeError("event_not_json_serializable");
    }
    for (let index = 0; index < value.length; index += 1) {
      const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
      if (!descriptor || !descriptor.enumerable || !Object.hasOwn(descriptor, "value")) {
        throw new TypeError("event_not_json_serializable");
      }
      assertJsonValue(descriptor.value, seen, depth + 1);
    }
  } else {
    if (!isPlainObject(value) || Object.getOwnPropertySymbols(value).length) {
      throw new TypeError("event_not_json_serializable");
    }
    for (const key of Object.keys(value)) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor || !descriptor.enumerable || !Object.hasOwn(descriptor, "value")) {
        throw new TypeError("event_not_json_serializable");
      }
      assertJsonValue(descriptor.value, seen, depth + 1);
    }
  }
  seen.delete(value);
}

function canonicalJson(value) {
  if (Array.isArray(value)) return "[" + value.map(canonicalJson).join(",") + "]";
  if (isPlainObject(value)) {
    return "{" + Object.keys(value).sort().map((key) =>
      JSON.stringify(key) + ":" + canonicalJson(value[key])
    ).join(",") + "}";
  }
  return JSON.stringify(value);
}

function normalizeEvent(input, maxEventBytes) {
  if (isPlainObject(input)) {
    const descriptor = Object.getOwnPropertyDescriptor(input, "payload");
    if (descriptor && (!descriptor.enumerable || !Object.hasOwn(descriptor, "value"))) {
      throw new TypeError("event_not_json_serializable");
    }
    if (descriptor?.value !== undefined) assertJsonValue(descriptor.value);
  }
  const event = createAgentEvent(input);
  assertJsonValue(event);
  const serialized = JSON.stringify(event);
  if (Buffer.byteLength(serialized, "utf8") > maxEventBytes) {
    throw new TypeError("event_too_large");
  }
  return {
    event: JSON.parse(serialized),
    eventHash: digest(canonicalJson(event)),
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
  await mkdir(directory, {recursive: true, mode: 0o700});
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
  await syncDirectory(directory);
  await syncDirectory(path.dirname(directory));
}

async function ensurePrivateDirectory(directory) {
  await mkdir(directory, {recursive: true, mode: 0o700});
  const stat = await lstat(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new Error("event_store_unsafe_path");
  }
  await chmod(directory, 0o700);
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
      digest(record.eventId) !== expectedHash ||
      !Number.isSafeInteger(record.order) ||
      record.order < 1 ||
      typeof record.eventHash !== "string" ||
      !HASH_PATTERN.test(record.eventHash)) {
    throw new Error("event_store_corrupt");
  }

  if (kind === "event") {
    let event;
    try {
      assertJsonValue(record.event?.payload ?? {});
      event = createAgentEvent(record.event);
      assertJsonValue(event);
    } catch {
      throw new Error("event_store_corrupt");
    }
    if (event.eventId !== record.eventId ||
        canonicalJson(event) !== canonicalJson(record.event) ||
        digest(canonicalJson(event)) !== record.eventHash) {
      throw new Error("event_store_corrupt");
    }
    return {kind, eventId: record.eventId, eventHash: record.eventHash, order: record.order, event};
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
  try {
    handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(JSON.stringify(record) + "\n", "utf8");
    await handle.sync();
  } finally {
    await handle?.close().catch(() => {});
  }

  try {
    await link(temporary, target);
    return true;
  } catch (error) {
    if (error?.code === "EEXIST") return false;
    throw error;
  } finally {
    await unlink(temporary).catch(() => {});
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
        const hash = digest(event.eventId);
        const pendingPath = path.join(this.#pendingDirectory, hash + ".json");
        const acknowledgedPath = path.join(this.#acknowledgedDirectory, hash + ".json");

        const acknowledged = await readJsonRecord(
          acknowledgedPath, "ack", hash, this.#maxEventBytes,
        );
        if (acknowledged) {
          if (acknowledged.eventHash !== eventHash) throw new Error("event_id_conflict");
          this.#lastOrder = Math.max(this.#lastOrder, acknowledged.order);
          return {inserted: false, order: acknowledged.order};
        }

        const pending = await readJsonRecord(
          pendingPath, "event", hash, this.#maxEventBytes,
        );
        if (pending) {
          if (pending.eventHash !== eventHash) throw new Error("event_id_conflict");
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
        const pending = await this.#scanDirectory(this.#pendingDirectory, "event");
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
        const hash = digest(eventId);
        const pendingPath = path.join(this.#pendingDirectory, hash + ".json");
        const acknowledgedPath = path.join(this.#acknowledgedDirectory, hash + ".json");
        const existingAck = await readJsonRecord(
          acknowledgedPath, "ack", hash, this.#maxEventBytes,
        );
        if (existingAck) {
          await unlink(pendingPath).catch((error) => { if (!isMissing(error)) throw error; });
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
        if (created) await syncDirectory(this.#acknowledgedDirectory);
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
