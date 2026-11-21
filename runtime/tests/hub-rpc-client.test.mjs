import test from 'node:test';
import assert from 'node:assert/strict';
import { createHubRpcClient } from '../hub-rpc-client.mjs';

const config = { hubUrl: 'https://hub.example/mcp', memberToken: 'test-token' };

test('sends authenticated JSON-RPC and retains the MCP session id', async () => {
  const calls = [];
  const client = createHubRpcClient({
    getConfig: () => config,
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return new Response(JSON.stringify({ jsonrpc: '2.0', id: 1, result: { ok: true } }), {
        status: 200, headers: { 'mcp-session-id': 'session-1' },
      });
    },
  });
  assert.deepEqual(await client.rpc('initialize', { protocolVersion: '2025-03-26' }), { ok: true });
  assert.equal(calls[0].url, config.hubUrl);
  assert.equal(calls[0].options.headers.Authorization, 'Bearer test-token');
  assert.equal(calls[0].options.headers.Accept, 'application/json, text/event-stream');
  assert.equal(client.getSessionId(), 'session-1');
  await client.rpc('tools/call', { name: 'heartbeat', arguments: {} });
  assert.equal(calls[1].options.headers['Mcp-Session-Id'], 'session-1');
  assert.equal(JSON.parse(calls[1].options.body).id, 2);
});

test('parses the last data frame from an SSE response', async () => {
  const client = createHubRpcClient({
    getConfig: () => config,
    fetchImpl: async () => new Response(
      'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"value":1}}\n\n' +
      'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"value":2}}\n\n',
      { status: 200 },
    ),
  });
  assert.deepEqual(await client.rpc('tools/call', {}), { value: 2 });
});

test('marks transport failures for the existing retry policy and resets sessions', async () => {
  const networkError = new TypeError('fetch failed');
  const client = createHubRpcClient({
    getConfig: () => config,
    fetchImpl: async () => { throw networkError; },
    isNetworkFailure: error => error instanceof TypeError,
  });
  await assert.rejects(client.rpc('initialize', {}), error => error === networkError && error.hubNetworkFailure === true);
  client.resetSession();
  assert.equal(client.getSessionId(), null);
});

test('surfaces HTTP and JSON-RPC errors without converting them to success', async () => {
  const httpClient = createHubRpcClient({ getConfig: () => config, fetchImpl: async () => new Response('', { status: 503 }) });
  await assert.rejects(httpClient.rpc('initialize', {}), /hub HTTP 503/);
  const rpcClient = createHubRpcClient({
    getConfig: () => config,
    fetchImpl: async () => new Response(JSON.stringify({ error: { code: -1, message: 'denied' } }), { status: 200 }),
  });
  await assert.rejects(rpcClient.rpc('initialize', {}), /hub rpc error/);
});
