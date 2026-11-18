"""staging20 watcher 模板直发预演：种子（成员+模板）→ 直插 owner 消息 →
等 watcher 轮询 → 校验任务与回帖。hub 与 watcher 进程由外层 shell 拉起。"""
import asyncio, json, sqlite3, sys, time
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "http://127.0.0.1:8818/mcp"
DISP = open("/home/hatch/workspace/pack/hub-staging20/.dispatcher_token").read().strip()
DB = "/home/hatch/workspace/pack/hub-staging20/pack.db"
PASS = []
FAIL = []

def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra else ""))

def client(tok):
    return Client(StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {tok}"}))

async def call(tok, tool, **kw):
    async with client(tok) as c:
        try:
            r = await c.call_tool(tool, kw)
            txt = r.content[0].text if r.content else ""
            try:
                return True, json.loads(txt)
            except Exception:
                return True, txt
        except Exception as e:
            return False, str(e)

async def seed():
    # 种子：成员 absent / owner + 模板「出图」（watcher 启动前完成）
    for name in ("absent", "owner"):
        ok, out = await call(DISP, "member_register", name=name)
        print("register", name, ok, "" if ok else out)
    ok, out = await call(DISP, "template_upsert", name="出图",
                         title_tpl="[ENG-image] 出图：{arg}",
                         body_tpl="按描述出图：{arg}", assignee="absent")
    check("seed template", ok, str(out))
    print(f"RESULT pass={len(PASS)} fail={len(FAIL)}")
    return 1 if FAIL else 0

async def main():
    # 直插 owner 消息（模拟 App 以 owner 身份 room_post 的落库结果）
    conn = sqlite3.connect(DB)
    owner_id = conn.execute("SELECT id FROM members WHERE name='owner'").fetchone()[0]
    room_id = conn.execute("SELECT id FROM rooms WHERE name='pack-main'").fetchone()[0]
    msgs = [
        "模板：出图 一只橙色的狐狸，雪山背景",
        "模板：出图",
        "模板：不存在的 x",
        "@absent 普通消息测试",
        "模板：出 一只猫",
    ]
    for b in msgs:
        conn.execute("INSERT INTO messages(room_id, author_id, kind, body, created_at) VALUES (?,?, 'chat', ?, ?)",
                     (room_id, owner_id, b, time.time()))
    conn.commit(); conn.close()

    # 等 watcher 两轮轮询
    await asyncio.sleep(25)

    ok, out = await call(DISP, "task_list")
    tasks = out["tasks"]
    tpl_tasks = [t for t in tasks if t["title"].startswith("[ENG-image] 出图：")]
    check("template hit creates task", any(t["title"] == "[ENG-image] 出图：一只橙色的狐狸，雪山背景" for t in tpl_tasks), str([t["title"] for t in tasks]))
    check("template assignee absent", all(t["assignee"] == "absent" for t in tpl_tasks))
    check("prefix template hit", any(t["title"] == "[ENG-image] 出图：一只猫" for t in tpl_tasks))
    check("no-arg template made NO task", len(tpl_tasks) == 2, f"tpl_tasks={len(tpl_tasks)}")
    at_tasks = [t for t in tasks if "普通消息测试" in t["title"]]
    check("@ flow regression", len(at_tasks) == 1 and at_tasks[0]["assignee"] == "absent")

    ok, out = await call(DISP, "room_read_new", room="pack-main", since_id=0, limit=100)
    bodies = [m["body"] for m in out["messages"] if m["author"] == "dispatcher"]
    check("reply: fired", any("已按模板「出图」开卡" in b for b in bodies), str(bodies))
    check("reply: no-arg failure explained", any("没开成卡" in b for b in bodies))
    check("reply: unknown template lists avail", any("没有叫「不存在的」的模板" in b and "出图" in b for b in bodies))
    check("reply: @ converted", any("已转任务" in b for b in bodies))

    print(f"RESULT pass={len(PASS)} fail={len(FAIL)}")
    return 1 if FAIL else 0

if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "verify"
    sys.exit(asyncio.run(seed() if phase == "seed" else main()))
