"""staging24 组合演练：新成员入群（头像组合不撞）+ 多房间 watcher
（room-b 模板开卡 / 归档房间不处理 / 主房间 @ 照常）+ 真实钟排班。
阶段：seed（watcher 启动前）→ verify（插消息+排班，等真实时间流逝后校验）。"""
import asyncio, json, os, sqlite3, sys, time
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging24")
import harness
from harness import call

DB = "/home/hatch/workspace/pack/hub-staging24/pack.db"
D = harness.DISP
TOKENS = {}
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra else ""))

async def seed():
    for name in ("absent", "owner", "scout"):
        ok, out = await call(D, "member_register", name=name)
        TOKENS[name] = out["token"]
        print("register", name, ok)
    with open("/home/hatch/workspace/pack/hub-staging24/tokens.json", "w") as f:
        json.dump(TOKENS, f)
    os.chmod("/home/hatch/workspace/pack/hub-staging24/tokens.json", 0o600)
    ok, out = await call(D, "member_list")
    combos = {}
    for m in out["members"]:
        av = m.get("avatar") or {}
        combos[m["name"]] = av.get("combo")
    check("4 members combos all distinct",
          len(set(combos.values())) == 4 and all(combos.values()), str(combos))
    ok, _ = await call(D, "room_create", name="room-b")
    ok2, _ = await call(D, "room_create", name="room-old")
    ok3, _ = await call(D, "room_archive", room="room-old")
    check("rooms created + room-old archived", ok and ok2 and ok3)
    ok, _ = await call(D, "template_upsert", name="出图",
                       title_tpl="[ENG-image] 出图：{arg}",
                       body_tpl="按描述出图：{arg}", assignee="absent")
    check("seed template", ok)
    print(f"RESULT pass={len(PASS)} fail={len(FAIL)}")
    return 1 if FAIL else 0

def insert_owner_msg(room, body):
    conn = sqlite3.connect(DB)
    oid = conn.execute("SELECT id FROM members WHERE name='owner'").fetchone()[0]
    rid = conn.execute("SELECT id FROM rooms WHERE name=?", (room,)).fetchone()[0]
    conn.execute("INSERT INTO messages(room_id, author_id, kind, body, created_at) VALUES (?,?, 'chat', ?, ?)",
                 (rid, oid, body, time.time()))
    conn.commit(); conn.close()

async def verify():
    TOKENS.update(json.load(open("/home/hatch/workspace/pack/hub-staging24/tokens.json")))
    A = TOKENS["absent"]
    # 排班：一张 60 秒后才放行的卡（真实钟）
    at = time.time() + 60
    ok, sch = await call(D, "task_schedule", room="pack-main", title="真实钟排班卡",
                         body="到点才能认领", member_name="absent", at_epoch=at)
    check("schedule created", ok, str(sch)[:70])
    sched_id = sch["task_id"]
    ok, _ = await call(A, "task_claim", task_id=sched_id)
    check("claim before time rejected", not ok)

    # 多房间消息（watcher 已在外层启动并完成初始化）
    insert_owner_msg("room-b", "模板：出图 房间二的狐狸")
    insert_owner_msg("room-old", "@absent 归档房间不该被处理")
    insert_owner_msg("pack-main", "@absent 主房间消息")
    await asyncio.sleep(25)

    ok, out = await call(D, "room_read_new", room="room-b", since_id=0, limit=50)
    bodies = [m["body"] for m in out["messages"]]
    check("room-b template fired", any("已按模板「出图」开卡" in b for b in bodies), str(bodies)[:120])
    ok, out = await call(D, "task_list")
    titles = [t["title"] for t in out["tasks"]]
    check("room-b task exists", any("房间二的狐狸" in t for t in titles), str(titles))
    check("archived room produced NO task", not any("归档房间" in t for t in titles))
    check("pack-main @ converted", any("主房间消息" in t for t in titles))

    # 等排班到点（真实钟，还剩约 35 秒）
    while time.time() < at + 3:
        await asyncio.sleep(3)
    ok, cl = await call(A, "task_claim", task_id=sched_id)
    check("claim after time succeeds", ok, str(cl)[:70])

    print(f"RESULT pass={len(PASS)} fail={len(FAIL)}")
    return 1 if FAIL else 0

if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) > 1 else "verify"
    sys.exit(asyncio.run(seed() if phase == "seed" else verify()))
