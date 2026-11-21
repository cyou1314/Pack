import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRuntimeStateStore } from '../runtime-state-store.mjs';

function withStore(run) {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'pack-runtime-state-'));
  try { run(dir, createRuntimeStateStore({ dir })); }
  finally { rmSync(dir, { recursive: true, force: true }); }
}

test('missing chat state and receipts retain their established defaults', () => withStore((_dir, store) => {
  assert.equal(store.loadChatState(), null);
  assert.deepEqual(store.loadDeliveryReceipts(), {});
}));

test('round-trips chat state and delivery receipts without changing their JSON shapes', () => withStore((dir, store) => {
  const chat = { last_chat_id: 42, chatUrl: 'https://chat.example/room' };
  const receipts = { '17': { taskId: 17, title: 'Task', body: 'Body', reply: 'Done', chatUrl: '', state: 'pending' } };
  store.saveChatState(chat);
  store.saveDeliveryReceipts(receipts);
  assert.deepEqual(store.loadChatState(), chat);
  assert.deepEqual(store.loadDeliveryReceipts(), receipts);
  assert.deepEqual(JSON.parse(readFileSync(path.join(dir, 'chat-state.json'), 'utf8')), chat);
  assert.deepEqual(JSON.parse(readFileSync(path.join(dir, 'delivery-receipts.json'), 'utf8')), receipts);
}));

test('writes state files with owner-only permissions and leaves no temporary files', () => withStore((dir, store) => {
  store.saveChatState({ last_chat_id: 1, chatUrl: null });
  store.saveDeliveryReceipts({});
  for (const file of ['chat-state.json', 'delivery-receipts.json']) {
    assert.equal(statSync(path.join(dir, file)).mode & 0o777, 0o600);
  }
  assert.deepEqual(readdirSync(dir).sort(), ['chat-state.json', 'delivery-receipts.json']);
}));

test('rejects malformed state rather than silently resetting it', () => withStore((dir, store) => {
  const file = path.join(dir, 'chat-state.json');
  writeFileSync(file, '{broken', 'utf8');
  assert.throws(() => store.loadChatState(), SyntaxError);
}));

test('rejects non-object state before writing', () => withStore((_dir, store) => {
  assert.throws(() => store.saveChatState(null), /must be an object/);
  assert.throws(() => store.saveDeliveryReceipts([]), /must be an object/);
}));
