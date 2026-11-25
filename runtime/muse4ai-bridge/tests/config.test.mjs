import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveRuntimeConfigPath, resolveMuseApiBaseUrl } from '../bridge.mjs';

const bridgeDir = path.dirname(fileURLToPath(new URL('../bridge.mjs', import.meta.url)));

test('bridge config defaults relative to its installed runtime directory', () => {
  assert.equal(resolveRuntimeConfigPath({}), path.resolve(bridgeDir, '..', 'config.json'));
  assert.equal(
    resolveRuntimeConfigPath({ PACK_RUNTIME_CONFIG: 'profiles/bridge.json' }),
    path.resolve(bridgeDir, 'profiles/bridge.json'),
  );
  assert.equal(
    resolveRuntimeConfigPath({ PACK_RUNTIME_CONFIG: '/tmp/pack-config.json' }),
    '/tmp/pack-config.json',
  );
});

test('Muse API endpoint defaults to loopback and accepts loopback overrides', () => {
  assert.equal(resolveMuseApiBaseUrl({}), 'http://127.0.0.1:47821');
  assert.equal(resolveMuseApiBaseUrl({ PACK_MUSE_API_URL: 'http://localhost:47822' }), 'http://localhost:47822');
  assert.equal(resolveMuseApiBaseUrl({ PACK_MUSE_API_URL: 'http://[::1]:47823' }), 'http://[::1]:47823');
});

test('Muse API endpoint rejects remote hosts, credentials and non-root paths', () => {
  for (const value of [
    'https://127.0.0.1:47821',
    'http://192.0.2.1:47821',
    'http://127.0.0.1:47821/v1',
    'http://user:pass@127.0.0.1:47821',
    'http://127.0.0.1:47821/?token=secret',
  ]) {
    assert.throws(() => resolveMuseApiBaseUrl({ PACK_MUSE_API_URL: value }), /invalid_local_muse_api_url/);
  }
});
