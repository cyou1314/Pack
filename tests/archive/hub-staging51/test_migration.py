import asyncio, json, os, shutil, socket, sqlite3, subprocess, sys, time
sys.path.insert(0, ".")
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.expanduser("~/workspace/pack/hub/.venv/bin/python")
OLD = os.path.join(HERE, "mig_old")
NEW = os.path.join(HERE, "mig_new")
res = []


def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)


def wait_port(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def start_server(cwd, port):
    log = open(os.path.join(cwd, "server.log"), "w")
    env = dict(os.environ, PACK_HUB_PORT=str(port))
    p = subprocess.Popen([VENV_PY, "hub.py", "serve"], cwd=cwd, env=env,
                         stdout=log, stderr=subprocess.STDOUT)
    assert wait_port(port), f"server on {port} did not start"
    return p


def client(port, tok):
    return Client(StreamableHttpTransport(
        f"http://127.0.0.1:{port}/mcp",
        headers={"Authorization": f"Bearer {tok}"}))


async def call(port, tok, tool, **kw):
    async with client(port, tok) as c:
        r = await c.call_tool(tool, kw)
        return json.loads(r.content[0].text)


async def main():
    for d in (OLD, NEW):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    shutil.copy(os.path.join(HERE, "hub_prod_baseline.py"),
                os.path.join(OLD, "hub.py"))
    # 1. 旧版建库 + 写数据
    subprocess.run([VENV_PY, "hub.py", "init"], cwd=OLD, capture_output=True, timeout=60)
    p = start_server(OLD, 18793)
    try:
        disp = open(os.path.join(OLD, ".dispatcher_token")).read().strip()
        async with client(18793, disp) as c:
            tools = await c.list_tools()
        note("migration: baseline hub has 65 tools", len(tools) == 65, f"got {len(tools)}")
        reg = await call(18793, disp, "member_register", name="legacy")
        await call(18793, reg["token"], "room_post", room="pack-main",
                   kind="chat", body="旧库里的一条消息")
        legacy_tok = reg["token"]
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
    # 2. 把旧库交给新版：init 迁移 + 起服务
    shutil.copy(os.path.join(HERE, "hub.py"), os.path.join(NEW, "hub.py"))
    for suf in ("", "-wal", "-shm"):
        src = os.path.join(OLD, "pack.db" + suf)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(NEW, "pack.db" + suf))
    subprocess.run([VENV_PY, "hub.py", "init"], cwd=NEW, capture_output=True, timeout=60)
    conn = sqlite3.connect(os.path.join(NEW, "pack.db"))
    mcols = {r[1] for r in conn.execute("PRAGMA table_info(members)")}
    msgcols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    note("migration: members gained 4 external columns",
         {"kind", "display_name", "bound_room_id", "joined_msg_id"} <= mcols)
    note("migration: messages gained reply_to_id", "reply_to_id" in msgcols)
    note("migration: new tables exist",
         {"pair_codes", "member_read_cursors", "hub_settings",
          "message_op_keys", "pair_attempts"} <= tables)
    row = conn.execute("SELECT value FROM hub_settings WHERE key='external_enabled'").fetchone()
    note("migration: external_enabled defaults on", row and row[0] == "1")
    leg = conn.execute("SELECT kind, name FROM members WHERE name='legacy'").fetchone()
    note("migration: legacy member intact, kind NULL", leg is not None and leg[0] is None)
    nmsg = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    note("migration: legacy messages intact", nmsg >= 1, f"n={nmsg}")
    p = start_server(NEW, 18792)
    try:
        disp = open(os.path.join(OLD, ".dispatcher_token")).read().strip()
        async with client(18792, disp) as c:
            tools = await c.list_tools()
        note("migration: upgraded hub serves 71 tools", len(tools) == 71,
             f"got {len(tools)}")
        hb = await call(18792, legacy_tok, "heartbeat")
        note("migration: legacy token still works", hb.get("member") == "legacy")
        ml = await call(18792, disp, "member_list")
        le = [m for m in ml["members"] if m["name"] == "legacy"][0]
        note("migration: legacy member reads as internal in member_list",
             le["kind"] == "internal" and le["display_name"] == "legacy"
             and le["bound_room_id"] is None)
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
    print(f"== {sum(res)}/{len(res)} PASS", flush=True)
    if not all(res):
        sys.exit(1)


asyncio.run(main())
