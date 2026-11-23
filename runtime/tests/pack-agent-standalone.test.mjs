import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const source = new URL("../pack-agent.mjs", import.meta.url);

test("pack-agent remains installable as a standalone single file", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "pack-agent-standalone-"));
  try {
    const copy = path.join(directory, "pack-agent.mjs");
    fs.copyFileSync(source, copy);
    const result = spawnSync(process.execPath, [copy], { encoding: "utf8" });

    assert.equal(result.status, 1);
    assert.match(result.stderr, /usage: pack-agent\.mjs/);
    assert.doesNotMatch(result.stderr, /ERR_MODULE_NOT_FOUND/);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
