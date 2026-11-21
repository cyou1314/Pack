import { chmodSync, renameSync, writeFileSync, readFileSync } from 'node:fs';
import path from 'node:path';

function readObject(filePath, fallback) {
  try {
    const value = JSON.parse(readFileSync(filePath, 'utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error(`invalid state object: ${path.basename(filePath)}`);
    }
    return value;
  } catch (error) {
    if (error.code === 'ENOENT') return fallback;
    throw error;
  }
}

function writeObject(filePath, value) {
  const temporaryPath = `${filePath}.tmp`;
  writeFileSync(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  chmodSync(temporaryPath, 0o600);
  renameSync(temporaryPath, filePath);
}

// Owns the on-disk JSON state format; callers retain control of state transitions.
export function createRuntimeStateStore({ dir, readFile = readFileSync, writeFile = writeFileSync, chmod = chmodSync, rename = renameSync } = {}) {
  if (typeof dir !== 'string' || !dir) throw new TypeError('runtime state directory is required');
  const chatPath = path.join(dir, 'chat-state.json');
  const receiptsPath = path.join(dir, 'delivery-receipts.json');

  function load(filePath, fallback) {
    try {
      const value = JSON.parse(readFile(filePath, 'utf8'));
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error(`invalid state object: ${path.basename(filePath)}`);
      }
      return value;
    } catch (error) {
      if (error.code === 'ENOENT') return fallback;
      throw error;
    }
  }

  function save(filePath, value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new TypeError('runtime state must be an object');
    }
    const temporaryPath = `${filePath}.tmp`;
    writeFile(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    chmod(temporaryPath, 0o600);
    rename(temporaryPath, filePath);
  }

  return {
    loadChatState: () => load(chatPath, null),
    saveChatState: value => save(chatPath, value),
    loadDeliveryReceipts: () => load(receiptsPath, {}),
    saveDeliveryReceipts: value => save(receiptsPath, value),
  };
}
