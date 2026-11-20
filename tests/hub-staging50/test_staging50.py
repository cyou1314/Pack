import asyncio, os, sqlite3, sys
sys.path.insert(0, ".")
from harness import call, client, DISP

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "pack.db")
res = []
def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)

def dbx(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.execute(sql, args); conn.commit(); conn.close()

async def main():
    async with client(DISP) as c:
        tools = await c.list_tools()
    note("tools == 65", len(tools) == 65, f"got {len(tools)}")

    reg = await call(DISP, "member_register", name="m50")
    mtok = reg["token"]
    t1 = (await call(DISP, "task_create", room="pack-main", title="s50 open", body="b", assignee_name="m50"))["task_id"]
    t2 = (await call(DISP, "task_create", room="pack-main", title="s50 claimed", body="b", assignee_name="m50"))["task_id"]
    t3 = (await call(DISP, "task_create", room="pack-main", title="s50 done", body="b", assignee_name="m50"))["task_id"]
    t4 = (await call(DISP, "task_create", room="pack-main", title="s50 cancelled", body="b", assignee_name="m50"))["task_id"]
    await call(mtok, "task_claim", task_id=t2)
    dbx("UPDATE tasks SET status='done' WHERE id=?", (t3,))
    dbx("UPDATE tasks SET status='cancelled' WHERE id=?", (t4,))

    async def ids(**kw):
        r = await call(DISP, "task_list", **kw)
        return sorted(x["id"] for x in r["tasks"])
    note("multi status open,claimed", await ids(status="open,claimed") == sorted([t1, t2]))
    note("single status done unchanged", await ids(status="done") == [t3])
    note("no status returns all", await ids() == sorted([t1, t2, t3, t4]))
    note("spaced multi status", await ids(status=" done , open ") == sorted([t1, t3]))
    note("comma-only status = no filter", await ids(status=",") == sorted([t1, t2, t3, t4]))
    note("multi status + room filter compose", await ids(status="open,claimed", room="pack-main") == sorted([t1, t2]))

    mids = []
    for i in range(6):
        r = await call(DISP, "room_post", room="pack-main", kind="chat", body=f"s50 msg {i+1}")
        mids.append(r["message_id"])
    async def rids(**kw):
        r = await call(DISP, "room_read_new", room="pack-main", **kw)
        return [x["id"] for x in r["messages"]]
    all_ids = await rids(limit=200)
    note("fixture: 4 task cards + 6 chats", len(all_ids) == 10, f"got {len(all_ids)}")
    note("tail=False first 3 asc", await rids(limit=3) == all_ids[:3])
    note("tail=True latest 3 asc", await rids(limit=3, tail=True) == mids[-3:])
    note("tail=True since_id respected", await rids(since_id=mids[3], limit=50, tail=True) == mids[4:])
    note("tail=False since_id unchanged", await rids(since_id=mids[3], limit=50) == mids[4:])
    note("tail=True limit=200 gets all", await rids(limit=200, tail=True) == all_ids)

    print(f"== {sum(res)}/{len(res)} PASS")
    if not all(res):
        sys.exit(1)

asyncio.run(main())
