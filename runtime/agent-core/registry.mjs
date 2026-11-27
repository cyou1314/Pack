import {assertAdapterInstance, defineAdapterDescriptor} from "./contract.mjs";

export class AgentAdapterRegistry {
  #entries = new Map();

  register({descriptor, create}) {
    if (typeof create !== "function") throw new TypeError("adapter_factory_required");
    const normalized = defineAdapterDescriptor(descriptor);
    if (this.#entries.has(normalized.adapterId)) {
      throw new Error("adapter_already_registered:" + normalized.adapterId);
    }
    this.#entries.set(normalized.adapterId, {
      descriptor: normalized,
      create,
    });
    return normalized;
  }

  get(adapterId) {
    const entry = this.#entries.get(adapterId);
    return entry ? entry.descriptor : null;
  }

  list({providerId, mode} = {}) {
    return [...this.#entries.values()]
      .map((entry) => entry.descriptor)
      .filter((item) => !providerId || item.providerId === providerId)
      .filter((item) => !mode || item.mode === mode)
      .sort((a, b) => a.adapterId.localeCompare(b.adapterId));
  }

  async create(adapterId, context = {}) {
    const entry = this.#entries.get(adapterId);
    if (!entry) throw new Error("adapter_not_found:" + adapterId);
    const instance = await entry.create(context);
    return assertAdapterInstance(entry.descriptor, instance);
  }

  has(adapterId) {
    return this.#entries.has(adapterId);
  }

  get size() {
    return this.#entries.size;
  }
}