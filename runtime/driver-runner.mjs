import { readFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import path from 'node:path';

// Owns only local ego-browser process composition/execution; task policy stays in runtime.mjs.
export function createDriverRunner({ dir, cfg, spawnImpl = spawn, readFile = readFileSync }) {
  return function runDriver(message, signal, chat = null) {
    // ego-browser nodejs reads program source from stdin (no file argument),
    // so compose: task injection + primitives (Mac-local) + core flow.
    const primitives = readFile(path.join(dir, "driver-primitives.js"), "utf8");
    const core = readFile(path.join(dir, chat?.action === "quota" ? "quota-driver.js" : chat ? "chat-driver.js" : "driver-core.js"), "utf8");
    const task = { profileId: cfg.profileId, spaceName: cfg.spaceName, message, ...(chat || {}) };
    const source = `globalThis.PACK_TASK = ${JSON.stringify(task)};\n${primitives}\n${core}`;
    return new Promise((resolve, reject) => {
      const child = spawnImpl(cfg.egoBin || "ego-browser", ["nodejs"], {
        stdio: ["pipe", "pipe", "pipe"],
      });
      let out = "", err = "";
      if (signal) { if (signal.aborted) child.kill(); else signal.addEventListener("abort", () => child.kill(), {once:true}); }
      child.stdout.on("data", (data) => (out += data));
      child.stderr.on("data", (data) => (err += data));
      child.on("error", reject);
      child.on("close", (code) => {
        const line = (out + "\n" + err).split("\n").filter((value) => value.startsWith("DRIVER-RESULT")).pop();
        if (!line) return reject(new Error(`driver no result (code ${code}): ${err.slice(0, 300)}`));
        try { resolve(JSON.parse(line.slice("DRIVER-RESULT".length).trim())); }
        catch { reject(new Error("driver result not json")); }
      });
      child.stdin.write(source);
      child.stdin.end();
    });
  };
}
