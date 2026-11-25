'use strict';
// Local-only handoff: the socket carries only a member name and an opaque
// Keychain reference. Cookie bytes travel exclusively through the Keychain.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { execFile } = require('node:child_process');
const { DatabaseSync } = require('node:sqlite');
const { promisify } = require('node:util');
const run = promisify(execFile);
const SERVICE = 'com.cyou.pack.l2-handoff';
const SOCKET_DIR = process.env.PACK_L2_SOCKET_DIR || path.join(require('node:os').homedir(), '.pack', 'l2-ipc');
const SOCKET = path.join(SOCKET_DIR, 'handoff.sock');

function cookieEncryptionEnabled(frameworkPath) {
  const framework = frameworkPath || path.resolve(process.execPath, '../../Frameworks/Electron Framework.framework/Electron Framework');
  const marker = Buffer.from('dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX');
  let fd;
  try { fd = fs.openSync(framework, 'r'); } catch { return false; }
  try {
    const size = fs.fstatSync(fd).size, chunkSize = 1024 * 1024;
    let tail = Buffer.alloc(0), offset = 0, found = 0, enabled = false, lastPosition = -1;
    while (offset < size) {
      const previousOffset = offset;
      const part = Buffer.alloc(Math.min(chunkSize, size - offset));
      const read = fs.readSync(fd, part, 0, part.length, offset); if (!read) break;
      offset += read;
      const chunk = Buffer.concat([tail, part.subarray(0, read)]);
      const chunkStart = previousOffset - tail.length;
      let searchFrom = 0, index;
      while ((index = chunk.indexOf(marker, searchFrom)) >= 0) {
        const absolutePosition = chunkStart + index;
        searchFrom = index + 1;
        if (absolutePosition <= lastPosition) continue;
        if (index + marker.length + 4 > chunk.length) break;
        const start = index + marker.length;
        found++;
        enabled = chunk[start] === 1 && chunk[start + 1] >= 2 && chunk[start + 3] === 0x31;
        lastPosition = absolutePosition;
      }
      tail = chunk.subarray(Math.max(0, chunk.length - marker.length - 4));
    }
    return found === 1 && enabled;
  } catch {
    return false;
  } finally { fs.closeSync(fd); }
}

function cookieDetails(c) {
  if (!c || typeof c.name !== 'string' || typeof c.value !== 'string' || typeof c.domain !== 'string') throw Error('invalid cookie');
  const domain = c.domain.toLowerCase(), host = domain.replace(/^\./, '');
  if (!(host === 'muse.ai' || host.endsWith('.muse.ai'))) throw Error('invalid cookie domain');
  const cookiePath = typeof c.path === 'string' && c.path.startsWith('/') ? c.path : '/';
  const out = { url: `${c.secure ? 'https' : 'http'}://${host}${cookiePath}`, name: c.name, value: c.value, path: cookiePath, secure: !!c.secure, httpOnly: !!c.httpOnly };
  if (domain.startsWith('.')) out.domain = domain;
  if (typeof c.expirationDate === 'number' && Number.isFinite(c.expirationDate)) out.expirationDate = c.expirationDate;
  if (typeof c.sameSite === 'string') {
    const s = c.sameSite.toLowerCase();
    if (s === 'strict') out.sameSite = 'strict';
    else if (s === 'lax') out.sameSite = 'lax';
    else if (s === 'none') out.sameSite = 'no_restriction';
  }
  return out;
}

async function handoff(request, deps) {
  if (!request || typeof request.member !== 'string' || !request.member.trim() || request.member.length > 32 || request.member.trim().toLowerCase() === 'absent' || !/^[0-9a-f-]{36}$/i.test(request.ticket || '')) throw Error('invalid request');
  if (!deps.encryptionEnabled()) throw Error('cookie encryption is not enabled');
  const member = request.member.trim();
  const existing = deps.repo.listAccounts().find(a => a.alias.toLocaleLowerCase('vi') === member.toLocaleLowerCase('vi'));
  if (existing && (existing.state === 'busy' || deps.coord.leases?.has(existing.id) || deps.coord.pageRunsOn(existing.id) > 0)) throw Error('account is busy');
  const proxy = existing ? deps.repo.getProxy(existing.proxyId) : deps.repo.listProxies().find(p => p.health === 'healthy');
  if (!proxy || proxy.health !== 'healthy') throw Error('no healthy proxy');
  let bytes, originalError;
  try {
    const result = await run('/usr/bin/security', ['find-generic-password', '-s', SERVICE, '-a', request.ticket, '-w'], { encoding: 'buffer', timeout: 90000, maxBuffer: 4 * 1024 * 1024 });
    bytes = result.stdout;
    const decoded = JSON.parse(bytes.toString('utf8'));
    if (!Array.isArray(decoded) || !decoded.length || decoded.length > 500) throw Error('empty cookie set');
    const cookies = decoded.map(cookieDetails);
    const current = deps.repo.listAccounts().find(a => a.alias.toLocaleLowerCase('vi') === member.toLocaleLowerCase('vi'));
    if ((existing?.id || null) !== (current?.id || null)) throw Error('account changed');
    const account = existing || deps.coord.addAccount({ alias: member, avatar: 0, proxyId: proxy.id });
    const leases = deps.coord.leases;
    if (!(leases instanceof Map) || leases.has(account.id) || deps.coord.pageRunsOn(account.id) > 0) throw Error('account is busy');
    const lease = { kind: 'check' };
    leases.set(account.id, lease);
    let counts;
    try {
      const store = deps.accounts.sessionFor(account.id);
      for (const cookie of cookies) await store.cookies.set(cookie);
      await store.cookies.flushStore();
      const stored = await store.cookies.get({});
      if (cookies.some(c => !stored.some(s => s.name === c.name && s.value === c.value && s.path === c.path && (s.domain || '').replace(/^\./, '') === new URL(c.url).hostname))) throw Error('cookie verification failed');
      const file = path.join(deps.sessionData, 'Partitions', `acc-${account.id}`, 'Cookies');
      const db = new DatabaseSync(file, { readOnly: true });
      try { counts = db.prepare('SELECT count(*) total, sum(length(encrypted_value)>0) encrypted, sum(length(value)>0) plain FROM cookies').get(); }
      finally { db.close(); }
      if (!counts || Number(counts.total) === 0 || Number(counts.plain) !== 0 || Number(counts.encrypted) !== Number(counts.total)) throw Error('cookie encryption verification failed');
    } finally {
      if (leases.get(account.id) === lease) leases.delete(account.id);
    }
    const checked = await deps.coord.checkSession(account.id, true, 'l2-handoff');
    return { ok: true, account: account.alias, state: checked.state, encrypted: Number(counts.encrypted), check: checked.state === 'ready' ? 'ready' : 'not_ready' };
  } catch (error) { originalError = error; throw error; }
  finally {
    bytes?.fill(0);
    try { await run('/usr/bin/security', ['delete-generic-password', '-s', SERVICE, '-a', request.ticket], { timeout: 10000, maxBuffer: 16384 }); }
    catch {
      if (!originalError) throw Error('temporary keychain cleanup failed');
      console.error('[l2-handoff] temporary keychain cleanup failed after original error');
    }
  }
}

async function memberOperation(request, deps) {
  const member = typeof request.member === 'string' ? request.member.trim() : '';
  if (!member || member.length > 32) throw Error('invalid member');
  const account = deps.repo.listAccounts().find(a => a.alias.toLocaleLowerCase('vi') === member.toLocaleLowerCase('vi'));
  if (request.op === 'status') return { ok: true, state: account ? account.state : 'not_found' };
  if (request.op !== 'remove' || member.toLowerCase() === 'absent') throw Error('invalid operation');
  if (!account) return { ok: true, state: 'not_found' };
  if (account.state === 'busy' || deps.coord.leases?.has(account.id) || deps.coord.pageRunsOn(account.id) > 0) throw Error('account busy');
  const result = await deps.coord.removeAccount(account.id, false);
  if (!result.removed || deps.repo.listAccounts().some(a => a.id === account.id)) throw Error('account removal incomplete');
  await new Promise(resolve => setTimeout(resolve, 2000));
  const dir = path.join(deps.sessionData, 'Partitions', `acc-${account.id}`);
  if (fs.existsSync(dir)) throw Error('partition removal incomplete');
  return { ok: true, state: 'removed' };
}

async function startHandoff(deps) {
  fs.mkdirSync(SOCKET_DIR, { recursive: true, mode: 0o700 });
  const dir = fs.lstatSync(SOCKET_DIR);
  if (!dir.isDirectory() || dir.isSymbolicLink() || dir.uid !== process.getuid() || (dir.mode & 0o077)) throw Error('unsafe handoff directory');
  if (fs.existsSync(SOCKET)) {
    const old = fs.lstatSync(SOCKET);
    if (!old.isSocket() || old.uid !== process.getuid()) throw Error('unsafe handoff socket');
    const live = await new Promise(resolve => { const c = net.createConnection(SOCKET); c.once('connect', () => { c.destroy(); resolve(true); }); c.once('error', () => resolve(false)); });
    if (live) throw Error('handoff socket already active');
    fs.unlinkSync(SOCKET);
  }
  const active = new Set();
  // App closes its write side after the request; keep our write side open
  // until asynchronous Keychain and session work has produced a reply.
  const server = net.createServer({ allowHalfOpen: true }, socket => {
    active.add(socket); socket.once('close', () => active.delete(socket));
    socket.setTimeout(240000, () => socket.destroy());
    let request = Buffer.alloc(0), handled = false;
    socket.on('data', chunk => {
      if (handled) return;
      request = Buffer.concat([request, chunk]);
      if (request.length > 4096) { handled = true; socket.end('{"ok":false,"error":"invalid request"}\n'); return; }
      const end = request.indexOf(10); if (end < 0) return;
      handled = true;
      let parsed; try { parsed = JSON.parse(request.subarray(0, end).toString('utf8')); } catch { socket.end('{"ok":false,"error":"invalid request"}\n'); return; }
      (parsed.op ? memberOperation(parsed, deps) : handoff(parsed, deps)).then(result => socket.end(JSON.stringify(result) + '\n')).catch(error => {
        const safe = /^(invalid request|invalid member|invalid operation|account busy|account is busy|account changed|account removal incomplete|partition removal incomplete|no healthy proxy|cookie encryption is not enabled|cookie verification failed|cookie encryption verification failed|temporary keychain cleanup failed)$/.test(error?.message || '') ? error.message : 'internal or keychain error';
        console.error(`[l2-handoff] request failed: ${safe}`);
        socket.end('{"ok":false,"error":"会话交接或检查失败，请检查引擎及钥匙串授权"}\n');
      });
    });
    socket.on('error', () => {});
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(SOCKET, () => { server.off('error', reject); resolve(); }); });
  fs.chmodSync(SOCKET, 0o600);
  return () => { for (const socket of active) socket.destroy(); server.close(); try { if (fs.lstatSync(SOCKET).isSocket()) fs.unlinkSync(SOCKET); } catch {} };
}
module.exports = { startHandoff, cookieEncryptionEnabled, cookieDetails, memberOperation };
