"""staging53 迁移测试：生产基线（hub_prod_baseline.py = 当前生产字节）
建的库交给 staging53 hub.py —— init 应补出 messages.payload 列、旧消息
可读（payload 为 null）、新 question 全链在迁移后的库上可用。
自带双服务（18795 旧 / 18796 新）、自清理。"""
import asyncio, json, os, shutil, socket, sqlite3, subprocess, sys, time
sys.path.insert(0, ".")
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.expanduser("~/workspace/pack/hub/.venv/bin/python")
OLD = os.path.join(HERE, "mig53_old")
NEW = os.path.join(HERE, "mig53_new")
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


async def main():
    for d in (OLD, NEW):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    shutil.copy(os.path.join(HERE, "hub_prod_baseline.py"),
                os.path.join(OLD, "hub.py"))
    # 1. 生产基线建库 + 写旧数据
    subprocess.run([VENV_PY, "hub.py", "init"], cwd=OLD, capture_output=True, timeout=60)
    cols = [r[1] for r in sqlite3.connect(os.path.join(OLD, "pack.db"))
            .execute("PRAGMA table_info(messages)").fetchall()]
    note("baseline db has no payload column", "payload" not in cols)
    p = start_server(OLD, 18795)
    try:
        disp = open(os.path.join(OLD, ".dispatcher_token")).read().strip()
        async with client(18795, disp) as c:
            tools = await c.list_tools()
        note("baseline hub has 74 tools", len(tools) == 74, f"got {len(tools)}")
        reg = await call(18795, disp, "member_register", name="legacy")
        legacy_tok = reg["token"]
        m = await call(18795, legacy_tok, "room_post", room="pack-main",
                       kind="chat", body="旧库里的一条消息")
        legacy_msg = m["message_id"]
    finally:
        stop_server(p)
    # 2. 旧库交给 staging53：init 迁移 + 起服务
    shutil.copy(os.path.join(HERE, "hub.py"), os.path.join(NEW, "hub.py"))
    for suf in ("", "-wal", "-shm"):
        src = os.path.join(OLD, "pack.db" + suf)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(NEW, "pack.db" + suf))
    subprocess.run([VENV_PY, "hub.py", "init"], cwd=NEW, capture_output=True, timeout=60)
    cols = [r[1] for r in sqlite3.connect(os.path.join(NEW, "pack.db"))
            .execute("PRAGMA table_info(messages)").fetchall()]
    note("migrated db has payload column", "payload" in cols)
    p = start_server(NEW, 18796)
    try:
        # 同一库同一调度员：token 沿用 OLD 的（init 对已有调度员不重写文件）
        disp = open(os.path.join(OLD, ".dispatcher_token")).read().strip()
        hb = await call(18796, legacy_tok, "heartbeat")
        note("legacy member survives migration", hb.get("member") == "legacy")
        rd = await call(18796, disp, "room_read_new", room="pack-main",
                        since_id=0, limit=50)
        by_id = {x["id"]: x for x in rd["messages"]}
        note("legacy message readable, payload null",
             legacy_msg in by_id and by_id[legacy_msg]["payload"] is None
             and by_id[legacy_msg]["body"] == "旧库里的一条消息")
        # 迁移后的库上跑通 question 全链
        tid = (await call(18796, disp, "task_create", room="pack-main",
                          title="mig task", body="b",
                          assignee_name="legacy"))["task_id"]
        await call(18796, legacy_tok, "task_claim", task_id=tid)
        q = await call(18796, legacy_tok, "room_post", room="pack-main",
                       kind="question", body=f"任务#{tid} 迁移后提问",
                       payload=json.dumps({"options": ["甲", "乙"]}))
        a = await call(18796, disp, "room_post", room="pack-main",
                       kind="question_answer", body="迁移后答复",
                       reply_to_id=q["message_id"])
        note("question chain works on migrated db", a["message_id"] > 0)
    finally:
        stop_server(p)
    print(f"== {sum(res)}/{len(res)} PASS")
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
