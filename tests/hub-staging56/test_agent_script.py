"""staging52 / 件四：pack-agent.mjs 脚本全链测试。

假设隔离中枢已在 18798 运行（run_tests.sh 起）。用临时 HOME 隔离
config，子进程跑 node pack-agent.mjs：pair → inbox → check-in →
send（防盲回退出码）→ mcp 代理 mark_read → send 成功 → read →
防串号 → 双 profile 隔离。
"""
import asyncio, json, os, shutil, stat, subprocess, sys, tempfile
sys.path.insert(0, ".")
from harness import call, pair_post, DISP

res = []
NODE = shutil.which("node")
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pack-agent.mjs")
HUB = "http://127.0.0.1:18798"


def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)


def run(env, *args, stdin=None):
    return subprocess.run([NODE, SCRIPT, *args], env=env, input=stdin,
                          capture_output=True, text=True, timeout=60)


async def make_code(name):
    r = await call(DISP, "pair_code_create", member_name=name, provider="codex",
                   room="pack-main", display_name="", reclaim=False)
    return r["pair_code"]


async def main():
    note("node available", NODE is not None, NODE or "")
    home = tempfile.mkdtemp(prefix="pack-agent-test-")
    env = dict(os.environ, HOME=home)
    cfg_prod = os.path.join(home, ".pack-agent", "prod.json")
    cfg_other = os.path.join(home, ".pack-agent", "other.json")

    # ---------- pair ----------
    code = await make_code("script-ext")
    p = run(env, "pair", code, "--hub", HUB, "--profile", "prod")
    note("pair: exit 0", p.returncode == 0, p.stderr[:120])
    note("pair: config written", os.path.exists(cfg_prod))
    mode = stat.S_IMODE(os.stat(cfg_prod).st_mode) if os.path.exists(cfg_prod) else None
    note("pair: config mode 0600", mode == 0o600, oct(mode) if mode else "")
    cfg = json.load(open(cfg_prod))
    note("pair: config identity", cfg.get("member") == "script-ext"
         and cfg.get("room") == "pack-main" and bool(cfg.get("token")))
    note("pair: token not echoed to stdout", cfg.get("token", "x") not in p.stdout)

    # ---------- inbox / check-in ----------
    await call(DISP, "room_post", room="pack-main", body="脚本测试普通消息")
    mm = await call(DISP, "room_post", room="pack-main", body="@script-ext 看这里")
    p = run(env, "inbox", "--profile", "prod")
    inbox = json.loads(p.stdout)
    bodies = [x["body"] for x in inbox["messages"]]
    note("inbox: pulls dispatcher messages",
         "脚本测试普通消息" in bodies and "@script-ext 看这里" in bodies, f"{bodies}")
    men = [x for x in inbox["messages"] if x["body"].startswith("@script-ext")]
    note("inbox: mention flagged", men and men[0]["mentions_me"] is True)
    p = run(env, "check-in", "--profile", "prod")
    note("check-in: todo exit code 2 with pending mention", p.returncode == 2,
         f"rc={p.returncode} out={p.stdout.strip()[:80]}")

    # ---------- send：防盲回退出码 → mcp 代理 mark_read → 发送成功 ----------
    p = run(env, "send", "脚本回复一", "--profile", "prod")
    note("send: blind-reply blocked exit 3", p.returncode == 3,
         f"rc={p.returncode} out={p.stdout.strip()[:80]}")
    rpc = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "inbox_mark_read", "arguments": {}}}) + "\n"
    p = run(env, "mcp", "--profile", "prod", stdin=rpc)
    note("mcp proxy: mark_read round-trips", '"cursor"' in p.stdout,
         p.stdout.strip()[:100])
    p = run(env, "send", "脚本回复一", "--profile", "prod")
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    note("send: succeeds after mark_read", p.returncode == 0 and out.get("sent") is True,
         f"rc={p.returncode} {out}")
    p = run(env, "read", str(mm["message_id"]), "--profile", "prod")
    note("read: fetches the @ message by id", "@script-ext 看这里" in p.stdout)

    # ---------- 防串号 ----------
    code2 = await make_code("script-other")
    p = run(env, "pair", code2, "--hub", HUB, "--profile", "prod")
    note("anti-mixup: pairing another member into same profile refused (exit 4)",
         p.returncode == 4, f"rc={p.returncode} err={p.stderr.strip()[:80]}")
    cfg2 = json.load(open(cfg_prod))
    note("anti-mixup: existing config untouched", cfg2.get("member") == "script-ext")

    # ---------- CLI mark-read 子命令（Gemini 复核必改 1：缺它游标永远推不动） ----------
    await call(DISP, "room_post", room="pack-main", body="@script-ext 再看这里")
    p = run(env, "check-in", "--profile", "prod")
    note("mark-read: new mention makes check-in todo again", p.returncode == 2,
         f"rc={p.returncode}")
    p = run(env, "mark-read", "--profile", "prod")
    mr = json.loads(p.stdout) if p.stdout.strip() else {}
    note("mark-read: CLI advances cursor", p.returncode == 0 and mr.get("cursor", 0) > 0,
         f"rc={p.returncode} out={p.stdout.strip()[:80]}")
    p = run(env, "inbox", "--profile", "prod")
    inbox2 = json.loads(p.stdout)
    note("mark-read: inbox empty after mark-read", inbox2["messages"] == [],
         f"n={len(inbox2['messages'])}")
    p = run(env, "check-in", "--profile", "prod")
    note("mark-read: check-in quiet after mark-read (no dead loop)",
         p.returncode == 0, f"rc={p.returncode} out={p.stdout.strip()[:80]}")

    # ---------- 双 profile 隔离 ----------
    code3 = await make_code("script-third")
    p = run(env, "pair", code3, "--hub", HUB, "--profile", "other")
    note("profiles: second profile pairs independently", p.returncode == 0,
         p.stderr[:120])
    cfg3 = json.load(open(cfg_other)) if os.path.exists(cfg_other) else {}
    cfg2 = json.load(open(cfg_prod))
    note("profiles: configs isolated",
         cfg3.get("member") == "script-third" and cfg2.get("member") == "script-ext"
         and cfg3.get("token") != cfg2.get("token"))
    p = run(env, "check-in", "--profile", "other")
    note("check-in: quiet profile exits 0", p.returncode == 0,
         f"rc={p.returncode} out={p.stdout.strip()[:80]}")

    shutil.rmtree(home, ignore_errors=True)
    print(f"== {sum(res)}/{len(res)} PASS")
    return 0 if all(res) else 1


sys.exit(asyncio.run(main()))
