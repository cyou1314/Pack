#!/usr/bin/env node
/**
 * Pack Mac runtime v1 (stage B1) - hosted member agent (absent).
 *
 * Loop: poll hub -> claim tasks assigned to this member -> deliver via ego
 * driver (driver-primitives.js + driver-core.js composed into ego-browser
 * nodejs stdin) -> extract reply only -> complete.
 * Red lines:
 *  - Tasks tagged with the approval marker wait for a dispatcher approval
 *    message in the room before executing (approval gate).
 *  - If the driver reports threadVerified=false, nothing counts as sent.
 *  - Only the extracted reply is posted; full page snapshots never leave
 *    the driver.
 * Config: config.json in same dir { hubUrl, memberToken, memberName,
 *   profileId, spaceName, pollSeconds, egoBin }
 * Smoke mode: node runtime.mjs --smoke "message"  (runs driver once)
 */
import { readFileSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DIR = path.dirname(fileURLToPath(import.meta.url));
const cfg = JSON.parse(readFileSync(path.join(DIR, "config.json"), "utf8"));
const POLL_MS = (cfg.pollSeconds || 20) * 1000;
// approval marker tag, built from char codes to survive any encoding path
const NEED_TAG = String.fromCharCode(0x3010, 0x9700, 0x6279, 0x51c6, 0x3011);

let sessionId = null;
let rpcId = 0;

async function rpc(method, params) {
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
    Authorization: `Bearer ${cfg.memberToken}`,
  };
  if (sessionId) headers["Mcp-Session-Id"] = sessionId;
  const res = await fetch(cfg.hubUrl, {
    method: "POST",
    headers,
    body: JSON.stringify({ jsonrpc: "2.0", id: ++rpcId, method, params }),
  });
  const sid = res.headers.get("mcp-session-id");
  if (sid) sessionId = sid;
  const text = await res.text();
  let payload = null;
  if (text.trim().startsWith("{")) payload = JSON.parse(text);
  else {
    for (const line of text.split("\n")) {
      if (line.startsWith("data:")) payload = JSON.parse(line.slice(5));
    }
  }
  if (!payload) throw new Error("hub: empty response");
  if (payload.error) throw new Error(`hub rpc error: ${JSON.stringify(payload.error)}`);
  return payload.result;
}

async function tool(name, args = {}) {
  const r = await rpc("tools/call", { name, arguments: args });
  if (r.isError) {
    const msg = r.content?.map((c) => c.text).join(" ") || "tool error";
    throw new Error(msg);
  }
  return r.structuredContent ?? JSON.parse(r.content[0].text);
}

async function mcpInit() {
  await rpc("initialize", {
    protocolVersion: "2025-03-26",
    capabilities: {},
    clientInfo: { name: "pack-runtime", version: "1.0.1" },
  });
  await fetch(cfg.hubUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      Authorization: `Bearer ${cfg.memberToken}`,
      ...(sessionId ? { "Mcp-Session-Id": sessionId } : {}),
    },
    body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }),
  }).catch(() => {});
}

function runDriver(message) {
  // ego-browser nodejs reads program source from stdin (no file argument),
  // so compose: task injection + primitives (Mac-local) + core flow.
  const primitives = readFileSync(path.join(DIR, "driver-primitives.js"), "utf8");
  const core = readFileSync(path.join(DIR, "driver-core.js"), "utf8");
  const task = { profileId: cfg.profileId, spaceName: cfg.spaceName, message };
  const source = `globalThis.PACK_TASK = ${JSON.stringify(task)};\n${primitives}\n${core}`;
  return new Promise((resolve, reject) => {
    const child = spawn(cfg.egoBin || "ego-browser", ["nodejs"], {
      stdio: ["pipe", "pipe", "pipe"],
    });
    let out = "", err = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (err += d));
    child.on("error", reject);
    child.on("close", (code) => {
      const line = out.split("\n").filter((l) => l.startsWith("DRIVER-RESULT")).pop();
      if (!line) return reject(new Error(`driver no result (code ${code}): ${err.slice(0, 300)}`));
      try { resolve(JSON.parse(line.slice("DRIVER-RESULT".length).trim())); }
      catch { reject(new Error("driver result not json")); }
    });
    child.stdin.write(source);
    child.stdin.end();
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const approvedWaits = new Map();
let lastMsgId = 0;

async function checkApprovals() {
  const r = await tool("room_read_new", { room: "pack-main", since_id: lastMsgId, limit: 50 });
  for (const m of r.messages) {
    lastMsgId = Math.max(lastMsgId, m.id);
    const match = m.body.match(/批准\s*任务#(\d+)/);
    if (match && m.author === "dispatcher") {
      const w = approvedWaits.get(Number(match[1]));
      if (w) w.granted = true;
    }
  }
}

async function processTask(task) {
  const claim = await tool("task_claim", { task_id: task.id });
  const ctok = claim.claim_token;
  const needsApproval = task.title.includes(NEED_TAG) || task.body.includes(NEED_TAG);
  if (needsApproval) {
    await tool("room_post", {
      room: "pack-main", kind: "approval_request",
      body: `任务#${task.id}「${task.title}」需要群主批准才能执行，请调度员确认。`,
    });
    await tool("task_update", { task_id: task.id, claim_token: ctok, note: "已提交批准，等待中" });
    const w = { granted: false };
    approvedWaits.set(task.id, w);
    for (let i = 0; i < 360 && !w.granted; i++) {
      await sleep(20000);
      await checkApprovals();
      if (i % 2 === 0) await tool("task_update", { task_id: task.id, claim_token: ctok, note: "" }).catch(() => {});
    }
    approvedWaits.delete(task.id);
    if (!w.granted) {
      await tool("task_block", { task_id: task.id, claim_token: ctok, reason: "等待批准超时（2 小时）" });
      return;
    }
  }
  await tool("task_update", { task_id: task.id, claim_token: ctok, note: "开始执行" });
  const prompt = `任务#${task.id}：${task.title}\n${task.body}\n完成后请把成果作为回复正文给出。`;
  let res;
  try {
    res = await runDriver(prompt);
  } catch (e) {
    await tool("task_block", { task_id: task.id, claim_token: ctok, reason: `驾驶层失败: ${e.message}` });
    return;
  }
  if (!res.ok) {
    await tool("task_block", { task_id: task.id, claim_token: ctok, reason: `驾驶层报告: ${res.error || "unknown"}` });
    return;
  }
  if (!res.threadVerified) {
    await tool("task_block", { task_id: task.id, claim_token: ctok, reason: "落点核对失败，未发送" });
    return;
  }
  await tool("task_complete", { task_id: task.id, claim_token: ctok, result: res.reply });
}

async function main() {
  if (process.argv[2] === "--smoke") {
    const msg = process.argv[3] || "Pack smoke test.";
    const res = await runDriver(msg);
    console.log("SMOKE-RESULT " + JSON.stringify(res));
    return;
  }
  await mcpInit();
  console.log(`pack-runtime up: member=${cfg.memberName} hub=${cfg.hubUrl}`);
  const boot = await tool("room_read_new", { room: "pack-main", since_id: 0, limit: 200 });
  for (const m of boot.messages) lastMsgId = Math.max(lastMsgId, m.id);
  const busy = new Set();
  for (;;) {
    try {
      await tool("heartbeat");
      await checkApprovals();
      const r = await tool("task_list", { status: "open", assignee_name: cfg.memberName });
      for (const t of r.tasks) {
        if (busy.has(t.id)) continue;
        busy.add(t.id);
        processTask(t).catch((e) => console.error("task", t.id, e.message)).finally(() => busy.delete(t.id));
      }
    } catch (e) {
      console.error("poll error:", e.message);
      sessionId = null;
      try { await mcpInit(); } catch {}
    }
    await sleep(POLL_MS);
  }
}

main();
