import asyncio, sqlite3, sys
sys.path.insert(0, ".")
from harness import call, DISP
from orphans import check_orphans
DB = "pack.db"
res = []
def note(name, ok, extra=""):
    res.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)

async def main():
    alice = (await call(DISP, "member_register", name="alice"))["token"]
    t = await call(DISP, "task_create", room="pack-main", title="D1 数据", body="正文", assignee_name="alice")
    tid = t["task_id"]
    cl = await call(alice, "task_claim", task_id=tid)
    await call(alice, "task_complete", task_id=tid, claim_token=cl["claim_token"], result="done")
    await call(DISP, "task_review", task_id=tid, approve=True, note="ok")
    await call(alice, "memory_write", scope="private", key="k1", text="v1")
    r = check_orphans(DB)
    note("clean data -> zero orphans", all(v == 0 for v in r.values()), str({k: v for k, v in r.items() if v}))
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO messages(room_id, author_id, kind, body, created_at) VALUES (9999, 1, 'chat', 'orphan', 0)")
    conn.execute("INSERT INTO runs(task_id, member_id, fence, status, started_at) VALUES (9999, 1, 1, 'running', 0)")
    conn.execute("INSERT INTO member_memory_versions(member, scope, \"key\", text, author, origin, size, created_at) VALUES ('alice', 'private', 'ghost', 'x', 'alice', 'test', 1, 0)")
    conn.commit(); conn.close()
    r = check_orphans(DB)
    note("injected message orphan flagged", r["messages.room_id"] == 1)
    note("injected run orphan flagged", r["runs.task_id"] == 1)
    note("injected version orphan flagged", r["member_memory_versions.no_current"] == 1)
    note("no false positives elsewhere", sum(r.values()) == 3, str({k: v for k, v in r.items() if v}))
    print("RESULT:", "ALL PASS" if all(res) else "FAILURES", flush=True)
    sys.exit(0 if all(res) else 1)
asyncio.run(main())
