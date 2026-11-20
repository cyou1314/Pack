#!/usr/bin/env node
// pack-agent.mjs — Pack 外部成员接入脚本（EXTERNAL-AGENT 件四）
// 单文件、零依赖、Node 18+。直连中枢 /mcp 说裸 JSON-RPC（initialize
// 握手 + Mcp-Session-Id，范式同 runtime.mjs），配对走公开 POST /pair。
// 每个请求带 User-Agent: pack-agent/<版本>（中枢版本门，件三）。
//
// 子命令：
//   pair <code> --hub <url> [--profile <名>] [--force]
//   inbox [--profile <名>]
//   mark-read [up_to_id] [--profile <名>]
//   read <id...> [--profile <名>]
//   send [--reply-to <id>] [--op-key <k>] [--ignore-new] [--profile <名>] <text...>
//   check-in [--profile <名>]        退出码：0=无待办 2=有待办 1=出错
//   mcp [--profile <名>]             stdio <-> /mcp 薄代理
// 配置：~/.pack-agent/<profile>.json（0600），--profile 隔离多身份/多中枢。
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";

const AGENT_VERSION = "1.0.0";
const UA = `pack-agent/${AGENT_VERSION}`;

// ---------------- 配置 ----------------

function configDir() {
  return path.join(os.homedir(), ".pack-agent");
}

function configPath(profile) {
  return path.join(configDir(), `${profile}.json`);
}

function loadConfig(profile) {
  const p = configPath(profile);
  if (!fs.existsSync(p)) return null;
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

function saveConfig(profile, cfg) {
  fs.mkdirSync(configDir(), { recursive: true, mode: 0o700 });
  const p = configPath(profile);
  fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + "\n", { mode: 0o600 });
  fs.chmodSync(p, 0o600); // 已存在文件时 writeFile 的 mode 不生效，补一刀
  return p;
}

function requireConfig(profile) {
  const cfg = loadConfig(profile);
  if (!cfg || !cfg.hub || !cfg.token) {
    throw new Error(`no config for profile "${profile}" — run pair first`);
  }
  return cfg;
}

// ---------------- /mcp JSON-RPC 客户端 ----------------

function parseRpcBody(text) {
  // 响应体可能是 SSE（data: 行）或裸 JSON；取最后一条 data 负载。
  let data = null;
  for (const line of String(text).split(/\r?\n/)) {
    if (line.startsWith("data:")) data = line.slice(5).trim();
  }
  const raw = data !== null ? data : String(text).trim();
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

class Mcp {
  constructor(hub, token) {
    this.url = hub.replace(/\/+$/, "") + "/mcp";
    this.token = token;
    this.sid = null;
    this.seq = 0;
  }

  async post(payload) {
    const headers = {
      "Content-Type": "application/json",
      "Accept": "application/json, text/event-stream",
      "User-Agent": UA,
    };
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
    if (this.sid) headers["Mcp-Session-Id"] = this.sid;
    const res = await fetch(this.url, {
      method: "POST", headers, body: JSON.stringify(payload),
    });
    const sid = res.headers.get("mcp-session-id");
    if (sid) this.sid = sid;
    return { status: res.status, text: await res.text() };
  }

  async init() {
    const { status, text } = await this.post({
      jsonrpc: "2.0", id: ++this.seq, method: "initialize",
      params: {
        protocolVersion: "2025-03-26", capabilities: {},
        clientInfo: { name: "pack-agent", version: AGENT_VERSION },
      },
    });
    if (status !== 200 || !this.sid) {
      throw new Error(`initialize failed: HTTP ${status} ${text.slice(0, 200)}`);
    }
    await this.post({ jsonrpc: "2.0", method: "notifications/initialized" });
  }

  async call(tool, args = {}) {
    if (!this.sid) await this.init();
    const { status, text } = await this.post({
      jsonrpc: "2.0", id: ++this.seq, method: "tools/call",
      params: { name: tool, arguments: args },
    });
    const payload = parseRpcBody(text);
    if (!payload) throw new Error(`bad hub response (HTTP ${status}): ${text.slice(0, 200)}`);
    if (payload.error) throw new Error(`RPC error: ${JSON.stringify(payload.error).slice(0, 300)}`);
    const result = payload.result;
    if (!result) throw new Error(`empty result (HTTP ${status})`);
    if (result.isError) {
      const t = result.content?.[0]?.text ?? "tool error";
      throw new Error(t);
    }
    const t = result.content?.[0]?.text ?? "";
    try { return JSON.parse(t); } catch { return t; }
  }
}

// ---------------- 子命令 ----------------

async function cmdPair(code, hub, profile, force) {
  if (!code) throw new Error("usage: pair <code> --hub <url>");
  if (!hub) throw new Error("pair requires --hub <url>");
  hub = hub.replace(/\/+$/, "");
  // 防串号：覆盖前先用已有 config 的 token 查一次身份（heartbeat 回
  // 本人 name）。已有的是另一个成员且未 --force → 拒绝覆盖落盘。
  const existing = loadConfig(profile);
  let oldName = null;
  if (existing && existing.token && existing.hub) {
    try {
      const hb = await new Mcp(existing.hub, existing.token).call("heartbeat");
      oldName = hb?.member ?? null;
    } catch { oldName = null; } // 旧 token 已失效/旧中枢不可达 → 不拦
  }
  const res = await fetch(hub + "/pair", {
    method: "POST",
    headers: { "Content-Type": "application/json", "User-Agent": UA },
    body: JSON.stringify({ code }),
  });
  const text = await res.text();
  let resp = null;
  try { resp = JSON.parse(text); } catch { /* 模糊错误体不一定是 JSON */ }
  if (res.status !== 200 || !resp || !resp.token) {
    throw new Error(`pair failed: HTTP ${res.status} ${text.slice(0, 200)}`);
  }
  if (oldName && resp.member && oldName !== resp.member && !force) {
    console.error(
      `refusing to overwrite profile "${profile}": it belongs to ${oldName}, ` +
      `the new code is for ${resp.member}. The code was redeemed but NOT saved; ` +
      `use --profile <other> or --force.`);
    process.exit(4);
  }
  const p = saveConfig(profile, {
    hub, token: resp.token, member: resp.member,
    display_name: resp.display_name ?? resp.member,
    room: resp.room ?? null, paired_at: new Date().toISOString(),
  });
  console.log(`paired as ${resp.member} (room ${resp.room}) -> ${p}`);
}

async function cmdInbox(profile) {
  const cfg = requireConfig(profile);
  const out = await new Mcp(cfg.hub, cfg.token).call("inbox_pull", {});
  console.log(JSON.stringify(out, null, 2));
}

async function cmdMarkRead(upToId, profile) {
  const cfg = requireConfig(profile);
  const args = {};
  if (upToId) {
    const n = parseInt(upToId, 10);
    if (!Number.isFinite(n) || n <= 0) throw new Error("usage: mark-read [up_to_id]");
    args.up_to_id = n;
  }
  const out = await new Mcp(cfg.hub, cfg.token).call("inbox_mark_read", args);
  console.log(JSON.stringify(out));
}

async function cmdRead(ids, profile) {
  if (!ids.length) throw new Error("usage: read <id...>");
  const cfg = requireConfig(profile);
  if (!cfg.room) throw new Error("config has no room (re-pair)");
  const want = ids.map((x) => parseInt(x, 10)).filter((n) => Number.isFinite(n));
  const since = Math.min(...want) - 1;
  const out = await new Mcp(cfg.hub, cfg.token).call(
    "room_read_new", { room: cfg.room, since_id: since, limit: 200 });
  const msgs = (out.messages ?? []).filter((m) => want.includes(m.id));
  console.log(JSON.stringify({ messages: msgs }, null, 2));
}

async function cmdSend(text, opts, profile) {
  if (!text) throw new Error("usage: send [--reply-to <id>] [--op-key <k>] [--ignore-new] <text>");
  const cfg = requireConfig(profile);
  const args = { body: text };
  if (opts.replyTo) args.reply_to_id = parseInt(opts.replyTo, 10);
  if (opts.opKey) args.op_key = opts.opKey;
  if (opts.ignoreNew) args.ignore_new = true;
  const out = await new Mcp(cfg.hub, cfg.token).call("inbox_send", args);
  console.log(JSON.stringify(out));
  if (out && out.sent === false) {
    console.error("blocked: new messages since your cursor (see new_messages); " +
      "read them first, or re-send with --ignore-new");
    process.exit(3);
  }
}

async function cmdCheckIn(profile) {
  const cfg = requireConfig(profile);
  const mcp = new Mcp(cfg.hub, cfg.token);
  const inbox = await mcp.call("inbox_pull", {});
  const msgs = inbox.messages ?? [];
  const mentions = msgs.filter((m) => m.mentions_me).length;
  let openTasks = 0;
  try {
    const tl = await mcp.call("task_list", { status: "open,claimed,working" });
    openTasks = (tl.tasks ?? []).length;
  } catch { openTasks = 0; }
  const todo = mentions > 0 || openTasks > 0;
  console.log(`check-in: unread=${msgs.length} mentions=${mentions} open_tasks=${openTasks} todo=${todo}`);
  console.log(JSON.stringify({ todo, unread: msgs.length, mentions, open_tasks: openTasks }));
  process.exit(todo ? 2 : 0);
}

async function cmdMcpProxy(profile) {
  const cfg = requireConfig(profile);
  const mcp = new Mcp(cfg.hub, cfg.token);
  const rl = readline.createInterface({ input: process.stdin });
  for await (const line of rl) {
    const s = line.trim();
    if (!s) continue;
    let msg;
    try { msg = JSON.parse(s); } catch { continue; }
    // 下游客户可能不发 initialize 直接发 tools/call：除 initialize
    // 本身外，任何消息转发前先确保上行会话已握手（initialize 由
    // post 的响应头捕获 session id，天然透传）。
    if (msg.method !== "initialize" && !mcp.sid) await mcp.init();
    const { text } = await mcp.post(msg);
    const payload = parseRpcBody(text);
    if (payload) process.stdout.write(JSON.stringify(payload) + "\n");
  }
}

// ---------------- 入口 ----------------

function parseArgs(argv) {
  const flags = { profile: "default", hub: null, force: false,
                  replyTo: null, opKey: null, ignoreNew: false };
  const pos = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--profile") flags.profile = argv[++i];
    else if (a.startsWith("--profile=")) flags.profile = a.slice(10);
    else if (a === "--hub") flags.hub = argv[++i];
    else if (a.startsWith("--hub=")) flags.hub = a.slice(6);
    else if (a === "--force") flags.force = true;
    else if (a === "--reply-to") flags.replyTo = argv[++i];
    else if (a === "--op-key") flags.opKey = argv[++i];
    else if (a === "--ignore-new") flags.ignoreNew = true;
    else pos.push(a);
  }
  return { flags, pos };
}

async function main() {
  const { flags, pos } = parseArgs(process.argv.slice(2));
  const cmd = pos[0];
  switch (cmd) {
    case "pair": return cmdPair(pos[1], flags.hub, flags.profile, flags.force);
    case "inbox": return cmdInbox(flags.profile);
    case "mark-read": return cmdMarkRead(pos[1], flags.profile);
    case "read": return cmdRead(pos.slice(1), flags.profile);
    case "send": return cmdSend(pos.slice(1).join(" "), flags, flags.profile);
    case "check-in": return cmdCheckIn(flags.profile);
    case "mcp": return cmdMcpProxy(flags.profile);
    default:
      console.error("usage: pack-agent.mjs <pair|inbox|mark-read|read|send|check-in|mcp> [args] [--profile <name>]");
      process.exit(1);
  }
}

main().catch((e) => { console.error(`error: ${e.message}`); process.exit(1); });
