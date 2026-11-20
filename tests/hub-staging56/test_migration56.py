"""staging56 迁移测试：生产基线（hub_prod_baseline.py = 当前生产字节，
74 工具、members 无 access_mode 列）建的库交给 staging56 hub.py ——
init 应补出 access_mode 列、存量成员读作 full 且行为不变、新工具与
模式闸在迁移后的库上可用；Phase A 配对链在迁移库上仍可跑通。
自带双服务（18795 旧 / 18796 新）、自清理。"""
import asyncio, json, os, shutil, socket, sqlite3, subprocess, sys, time
import urllib.request, urllib.error
sys.path.insert(0, ".")
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.expanduser("~/workspace/pack/hub/.venv/bin/python")
OLD = os.path.join(HERE, "mig56_old")
NEW = os.path.join(HERE, "mig56_new")
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


def stop_server(p):
    p.terminate()
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()


def client(port, tok):
    return Client(StreamableHttpTransport(
        f"http://127.0.0.1:{port}/mcp",
        headers={"Authorization": f"Bearer {tok}"}))


async def call(port, tok, tool, **kw):
    async with client(port, tok) as c:
        r = await c.call_tool(tool, kw)
        return json.loads(r.content[0].text)


async def try_call(port, tok, tool, **kw):
    try:
        return True, await call(port, tok, tool, **kw)
    except Exception as e:
        return False, str(e)


def pair_post(port, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/pair", data=data,
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def member_cols(dbpath):
    return [r[1] for r in sqlite3.connect(dbpath)
            .execute("PRAGMA table_info(members)").fetchall()]


async def main():
    for d in (OLD, NEW):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    shutil.copy(os.path.join(HERE, "hub_prod_baseline.py"),
                os.path.join(OLD, "hub.py"))
    # 1. 生产基线建库 + 写旧数据
    subprocess.run([VENV_PY, "hub.py", "init"], cwd=OLD, capture_output=True, timeout=60)
    note("baseline db has no access_mode column",
         "access_mode" not in member_cols(os.path.join(OLD, "pack.db")))
    p = start_server(OLD, 18795)
    try:
        disp = open(os.path.join(OLD, ".dispatcher_token")).read().strip()
        async with client(18795, disp) as c:
            tools = await c.list_tools()
        note("baseline hub has 74 tools", len(tools) == 74, f"got {len(tools)}")
        reg = await call(18795, disp, "member_register", name="legacy")
        legacy_tok = reg["token"]
        tid = (await call(18795, disp, "task_create", room="pack-main",
                          title="legacy task", body="b",
                          assignee_name="legacy"))["task_id"]
    finally:
        stop_server(p)
    # 2. 旧库交给 staging56：init 迁移 + 起服务
    shutil.copy(os.path.join(HERE, "hub.py"), os.path.join(NEW, "hub.py"))
    for suf in ("", "-wal", "-shm"):
        src = os.path.join(OLD, "pack.db" + suf)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(NEW, "pack.db" + suf))
    subprocess.run([VENV_PY, "hub.py", "init"], cwd=NEW, capture_output=True, timeout=60)
    note("migrated db has access_mode column",
         "access_mode" in member_cols(os.path.join(NEW, "pack.db")))
    raw = sqlite3.connect(os.path.join(NEW, "pack.db")).execute(
        "SELECT access_mode FROM members WHERE name='legacy'").fetchone()
    note("legacy row access_mode stays NULL in db (read-side normalization)",
         raw is not None and raw[0] is None)
    p = start_server(NEW, 18796)
    try:
        disp = open(os.path.join(OLD, ".dispatcher_token")).read().strip()
        async with client(18796, disp) as c:
            tools = await c.list_tools()
        note("upgraded hub serves 75 tools", len(tools) == 75,
             f"got {len(tools)}")
        ml = await call(18796, disp, "member_list")
        row = [m for m in ml["members"] if m["name"] == "legacy"][0]
        note("legacy member reads access_mode=full", row["access_mode"] == "full")
        await call(18796, legacy_tok, "task_claim", task_id=tid)
        note("legacy member keeps full behavior (claim ok)", True)
        # 模式闸在迁移库上可用
        await call(18796, disp, "member_access_mode_set",
                   name="legacy", mode="readonly")
        tid2 = (await call(18796, disp, "task_create", room="pack-main",
                           title="legacy task 2", body="b",
                           assignee_name="legacy"))["task_id"]
        ok, err = await try_call(18796, legacy_tok, "task_claim", task_id=tid2)
        note("mode gate works on migrated db",
             not ok and "access mode" in err, err[:70])
        await call(18796, disp, "member_access_mode_set",
                   name="legacy", mode="full")
        # Phase A 配对链在迁移库上仍可跑通
        pc = await call(18796, disp, "pair_code_create", member_name="mig-ext",
                        provider="codex", room="pack-main")
        st, resp = pair_post(18796, {"code": pc["pair_code"]})
        note("pair flow works on migrated db", st == 200, f"status={st}")
        if st == 200:
            got = await call(18796, resp["token"], "inbox_pull")
            note("paired external can inbox_pull on migrated db",
                 "messages" in got)
    finally:
        stop_server(p)
    print(f"== {sum(res)}/{len(res)} PASS")
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
