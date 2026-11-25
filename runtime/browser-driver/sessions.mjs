import fs from 'node:fs/promises';
import path from 'node:path';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function profileId(profile) {
  if (profile?.mode !== 'nativeAccount') throw Error('native_profile_required');
  if (typeof profile.id !== 'string' || !UUID.test(profile.id)) throw Error('invalid_profile_id');
  return profile.id.toLowerCase();
}

async function privateDirectory(directory, create = false) {
  if (create) {
    try { await fs.mkdir(directory, { mode: 0o700 }); }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  const st = await fs.lstat(directory);
  if (!st.isDirectory() || st.isSymbolicLink() || (st.mode & 0o077) !== 0 ||
      (typeof process.getuid === 'function' && st.uid !== process.getuid())) {
    throw Error('unsafe_session_directory');
  }
}

function deadline(promise, ms, code) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timer = setTimeout(() => reject(Error(code)), ms); })
  ]).finally(() => clearTimeout(timer));
}

/**
 * Dedicated native-account browser sessions. Root must be a pre-created
 * private Pack directory (not the user's everyday Chrome profile). The
 * exclusive lease also prevents two cooperating processes opening one profile.
 * Failed or ambiguous close keeps the lease; never blindly break stale leases.
 */
export class BrowserSessions {
  #root;
  #launch;
  #sessions = new Map();
  #launchTimeout;
  #closeTimeout;
  #headless;

  constructor({ root, launch, launchTimeoutMs = 30000, closeTimeoutMs = 10000, headless = false }) {
    if (typeof root !== 'string' || !path.isAbsolute(root) || typeof launch !== 'function' ||
        typeof headless !== 'boolean') throw Error('invalid_browser_configuration');
    for (const ms of [launchTimeoutMs, closeTimeoutMs]) {
      if (!Number.isSafeInteger(ms) || ms < 1 || ms > 120000) throw Error('invalid_browser_timeout');
    }
    this.#root = path.resolve(root);
    this.#launch = launch;
    this.#launchTimeout = launchTimeoutMs;
    this.#closeTimeout = closeTimeoutMs;
    this.#headless = headless;
  }

  status(profile) {
    return this.#sessions.get(profileId(profile))?.state ?? 'closed';
  }

  async open(profile) {
    const id = profileId(profile);
    if (this.#sessions.has(id)) throw Error('session_busy');
    const entry = { state: 'opening' };
    this.#sessions.set(id, entry);
    let launchAttempted = false;
    try {
      await privateDirectory(this.#root);
      // Reject aliases in the final root itself; real parent location is trusted.
      const root = await fs.realpath(this.#root);
      const directory = path.join(root, id);
      await privateDirectory(directory, true);
      const lease = path.join(directory, '.pack-session-lease');
      try { await fs.mkdir(lease, { mode: 0o700 }); }
      catch (error) {
        if (error.code === 'EEXIST') throw Error('session_lease_exists');
        throw error;
      }
      entry.lease = lease;
      const data = path.join(directory, 'browser-data');
      await privateDirectory(data, true);
      launchAttempted = true;
      const pending = Promise.resolve().then(() => this.#launch(data, {
        headless: this.#headless,
        timeout: this.#launchTimeout
      }));
      try {
        entry.context = await deadline(pending, this.#launchTimeout + 1000, 'browser_launch_timeout');
      } catch (error) {
        // Launch failure can leave a browser alive. If a late context arrives,
        // close it, but retain the lease for explicit recovery.
        pending.then(context => context.close()).catch(() => {});
        throw error;
      }
      entry.state = 'open';
      return entry.context;
    } catch (error) {
      if (launchAttempted) {
        entry.state = 'recoveryRequired';
      } else {
        if (entry.lease) {
          try { await fs.rmdir(entry.lease); }
          catch { entry.state = 'recoveryRequired'; throw error; }
        }
        this.#sessions.delete(id);
      }
      throw error;
    }
  }

  async close(profile) {
    const id = profileId(profile);
    const entry = this.#sessions.get(id);
    if (!entry) return;
    if (entry.state !== 'open') throw Error('session_not_open');
    entry.state = 'closing';
    try {
      await deadline(Promise.resolve().then(() => entry.context.close()), this.#closeTimeout, 'browser_close_timeout');
      await fs.rmdir(entry.lease);
      this.#sessions.delete(id);
    } catch (error) {
      entry.state = 'recoveryRequired';
      throw error;
    }
  }
}
