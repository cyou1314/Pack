#!/usr/bin/env node
/**
* Pack Mac 运行时 v1（阶段 B1）——托管成员 absent 的本地常驻件。
*
* 职责：轮询中枢 → 认领指派给本成员的任务 → 经 ego 驱动（driver.js）递进
* absent 的 muse.ai 对话 → 等回复 → 只提取回复原文交活。
* 红线落地：
* - 需批准的任务（标题/正文带标记）先在房间发 approval_request 并停手，
* 等调度员在房间里发出「批准 任务#N」才继续（批准门）。
* - 驾驶层若报告落点核对失败（threadVerified=false），绝不发送，task_block 报明。
* - 回复只取驾驶层提取的 reply 字段；整页快照不落盘、不回传。
* 配置：同目录 config.json { hubUrl, memberToken, memberName, profileId,
* spaceName, pollSeconds, egoBin}
*/
import { readFileSync} from "node:fs";
import { spawn} from "node:child_process";
import path from "node:path";
import { fileURLToPath} from "node:url";

const DIR = path.dirname(fileURLToPath(import.meta.url));
const cfg = JSON.parse(readFileSync(path.join(DIR, "config.json"), "utf8"));
const POLL_MS = (cfg.pollSeconds || 20) * 1000;

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
body: JSON.stringify({ jsonrpc: "2.0", id: ++rpcId, method, params}),
});
const sid = res.headers.get("mcp-session-id");
if (sid) sessionId = sid;
const text = await res.text();
// streamable HTTP 可能回 SSE，逐行找 data:
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
const r = await rpc("tools/call", { name, arguments: args});
if (r.isError) {
const msg = r.content?.map((c) => c.text).join(" ") || "tool error";
throw new Error(msg);
}
return r.structuredContent?? JSON.parse(r.content[0].text);
}

async function mcpInit() {
await rpc("initialize", {
protocolVersion: "2025-03-26",
capabilities: {},
clientInfo: { name: "pack-runtime", version: "1.0.0"},
});
await fetch(cfg.hubUrl, {
method: "POST",
headers: {
"Content-Type": "application/json",
Accept: "application/json, text/event-stream",
Authorization: `Bearer ${cfg.memberToken}`,
...(sessionId? { "Mcp-Session-Id": sessionId}: {}),
},
body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized"}),
}).catch(() => {});
}

function runDriver(message) {
return new Promise((resolve, reject) => {
const child = spawn(cfg.egoBin || "ego-browser", ["nodejs", path.join(DIR, "driver.js")], {
stdio: ["pipe", "pipe", "pipe"],
});
let out = "", err = "";
child.stdout.on("data", (d) => (out += d));
child.stderr.on("data", (d) => (err += d));
child.on("error", reject);
child.on("close", (code) => {
// 驾驶层约定：最后一行打印 DRIVER-RESULT {json}
const line = out.split("\n").filter((l) => l.startsWith("DRIVER-RESULT")).pop();
if (!line) return reject(new Error(`driver no result (code ${code}): ${err.slice(0, 300)}`));
try { resolve(JSON.parse(line.slice("DRIVER-RESULT".length).trim()));}
catch { reject(new Error("driver result not json"));}
});
child.stdin.write(JSON.stringify({
profileId: cfg.profileId, spaceName: cfg.spaceName, message,
}));
child.stdin.end();
});
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const approvedWaits = new Map(); // taskId -> {claimToken}
let lastMsgId = 0;

async function checkApprovals() {
const r = await tool("room_read_new", { room: "pack-main", since_id: lastMsgId, limit: 50});
for (const m of r.messages) {
lastMsgId = Math.max(lastMsgId, m.id);
const match = m.body.match(/批准\s*任务#(\d+)/);
if (match && m.author === "dispatcher") {
const tid = Number(match[1]);
const w = approvedWaits.get(tid);
if (w) { w.granted = true;}
}
}
}

async function processTask(task) {
const claim = await tool("task_claim", { task_id: task.id});
const ctok = claim.claim_token;
const needsApproval = //.test(task.title) || //.test(task.body);
if (needsApproval) {
await tool("room_post", {
room: "pack-main", kind: "approval_request",
body: `任务#${task.id}「${task.title}」需要群主批准才能执行，请调度员确认。`,
});
await tool("task_update", { task_id: task.id, claim_token: ctok, note: "已提交批准，等待中"});
const w = { granted: false};
approvedWaits.set(task.id, w);
// 等批准最多 2 小时（租约靠周期性 task_update 续）
for (let i = 0; i < 360 &&!w.granted; i++) {
await sleep(20000);
await checkApprovals();
if (i % 2 === 0) await tool("task_update", { task_id: task.id, claim_token: ctok, note: ""}).catch(() => {});
}
approvedWaits.delete(task.id);
if (!w.granted) {
await tool("task_block", { task_id: task.id, claim_token: ctok, reason: "等待批准超时（2 小时）"});
return;
}
}
await tool("task_update", { task_id: task.id, claim_token: ctok, note: "开始执行"});
const prompt = `任务#${task.id}：${task.title}\n${task.body}\n完成后请把成果作为回复正文给出。`;
let res;
try {
res = await runDriver(prompt);
} catch (e) {
await tool("task_block", { task_id: task.id, claim_token: ctok, reason: `驾驶层失败: ${e.message}`});
return;
}
if (!res.ok) {
await tool("task_block", { task_id: task.id, claim_token: ctok, reason: `驾驶层报告: ${res.error || "unknown"}`});
return;
}
if (!res.threadVerified) {
// 落点核对失败 = 绝不视为已发送（见规格附录 E 发现 1）
await tool("task_block", { task_id: task.id, claim_token: ctok, reason: "落点核对失败，未发送"});
return;
}
await tool("task_complete", { task_id: task.id, claim_token: ctok, result: res.reply});
}

async function main() {
await mcpInit();
console.log(`pack-runtime up: member=${cfg.memberName} hub=${cfg.hubUrl}`);
// 记录房间消息水位，避免把旧批准当新批准
const boot = await tool("room_read_new", { room: "pack-main", since_id: 0, limit: 200});
for (const m of boot.messages) lastMsgId = Math.max(lastMsgId, m.id);
const busy = new Set();
for (;;) {
try {
await tool("heartbeat");
await checkApprovals();
const r = await tool("task_list", { status: "open", assignee_name: cfg.memberName});
for (const t of r.tasks) {
if (busy.has(t.id)) continue;
busy.add(t.id);
processTask(t).catch((e) => console.error("task", t.id, e.message)).finally(() => busy.delete(t.id));
}
} catch (e) {
console.error("poll error:", e.message);
sessionId = null;
try { await mcpInit();} catch {}
}
await sleep(POLL_MS);
}
}

main();
