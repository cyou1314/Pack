import asyncio, json, os, sqlite3, subprocess, sys, time
sys.path.insert(0, ".")
from harness import call, call_err, client, DISP

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "pack.db")
ROOM = "pack-main"

res = []
def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)

def dbq(sql, args=()):
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    conn.close(); return rows

def dbx(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.execute(sql, args); conn.commit(); conn.close()

def sweep():
    r = subprocess.run([sys.executable, "hub.py", "sweep"], cwd=HERE,
                       env={**os.environ, "PACK_HUB_PORT": "18795"},
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"sweep failed: {r.stdout} {r.stderr}"

def member(name):
    return dbq("SELECT * FROM members WHERE name=?", (name,))[0]

def taskrow(tid):
    return dbq("SELECT * FROM tasks WHERE id=?", (tid,))[0]

def transfers(tid):
    return dbq("SELECT * FROM task_transfers WHERE task_id=? ORDER BY id", (tid,))

def audit_n(action, tid=None):
    if tid is None:
        return dbq("SELECT * FROM audit WHERE action=?", (action,))
    return dbq("SELECT * FROM audit WHERE action=? AND detail LIKE ?",
               (action, f"task {tid} %"))

async def members_by_name(tok=DISP):
    ml = await call(tok, "member_list")
    return {m["name"]: m for m in ml["members"]}

async def main():
    now = time.time()
    # ---------- 0 tool surface ----------
    async with client(DISP) as c:
        tools = await c.list_tools()
    names = [t.name for t in tools]
    note("0 tool count 65", len(names) == 65, f"got {len(names)}")
    note("0 member_quota_report present", "member_quota_report" in names)

    ra = await call(DISP, "member_register", name="shadow-a")
    rb = await call(DISP, "member_register", name="shadow-b")
    rc = await call(DISP, "member_register", name="shadow-c")
    A, B, C = ra["token"], rb["token"], rc["token"]

    # ---------- 1 self report only touches self ----------
    out = await call(A, "member_quota_report", week_used_pct=42, extra_tokens_left=12345)
    note("1 report returns own row", out["name"] == "shadow-a"
         and out["quota_week_used_pct"] == 42 and out["quota_extra_left"] == 12345)
    by = await members_by_name()
    note("1 list shows self values", by["shadow-a"]["quota_week_used_pct"] == 42
         and by["shadow-a"]["quota_extra_left"] == 12345)
    note("1 other member untouched", by["shadow-b"]["quota_week_used_pct"] is None)
    rows = dbq("SELECT * FROM audit WHERE action='member_quota_report'")
    note("1 audit source=self", len(rows) == 1 and "source=self" in (rows[0]["detail"] or "")
         and rows[0]["actor"] == "shadow-a", rows[0]["detail"] if rows else "none")
    e = await call_err(A, "member_quota_report", member_name="shadow-b", week_used_pct=1)
    note("1 no member_name path", e is not None)
    by = await members_by_name()
    note("1 b still untouched after spoof attempt", by["shadow-b"]["quota_week_used_pct"] is None)
    out = await call(A, "member_quota_report", week_reset_at=now + 7 * 86400)
    note("1 sane reset_at accepted", abs(out["quota_week_reset_at"] - (now + 7 * 86400)) < 2)
    await call(DISP, "member_quota_set", member_name="shadow-b", week_used_pct=33)
    rows = dbq("SELECT * FROM audit WHERE action='member_quota_set'")
    note("1 dispatcher set source=dispatcher", len(rows) >= 1
         and "source=dispatcher" in (rows[-1]["detail"] or ""), rows[-1]["detail"] if rows else "none")
    e = await call_err(A, "member_quota_set", member_name="shadow-a", week_used_pct=1)
    note("1 member cannot use dispatcher tool", e is not None)

    # ---------- 2 invalid self reports rejected wholesale ----------
    before = member("shadow-a")
    for kw in (dict(week_used_pct=150), dict(week_used_pct=-1),
               dict(extra_tokens_left=-5), dict(week_reset_at=99999999999999),
               dict(week_reset_at=12345), dict()):
        e = await call_err(A, "member_quota_report", **kw)
        assert e is not None, f"invalid report accepted: {kw}"
    after = member("shadow-a")
    note("2 invalid reports rejected, db unchanged",
         after["quota_week_used_pct"] == before["quota_week_used_pct"]
         and after["quota_extra_left"] == before["quota_extra_left"]
         and after["quota_updated_at"] == before["quota_updated_at"])

    # ---------- 3 quota block marks exhausted ----------
    for tok in (B, C):
        await call(tok, "heartbeat")
    await call(DISP, "member_quota_set", member_name="shadow-b", week_used_pct=20)
    await call(DISP, "member_quota_set", member_name="shadow-c", week_used_pct=30)
    t = await call(DISP, "task_create", room=ROOM, title="t3a job", body="work", assignee_name="shadow-a")
    cl = await call(A, "task_claim", task_id=t["task_id"])
    out = await call(A, "task_block", task_id=t["task_id"], claim_token=cl["claim_token"],
                     reason="quota out", fail_class="quota")
    note("3 block quota relays to lowest-quota candidate", out["relayed_to"] == "shadow-b", str(out))
    note("3 return flags exhaustion mark", out.get("quota_marked_exhausted") is True)
    by = await members_by_name()
    note("3 blocker marked 100 fresh", by["shadow-a"]["quota_week_used_pct"] == 100
         and time.time() - by["shadow-a"]["quota_updated_at"] < 60)
    note("3 exhausted audit written", len(audit_n("member_quota_exhausted", t["task_id"])) == 1)
    # b blocks its own task: exhausted a (100) must not be picked, c (30) is
    t = await call(DISP, "task_create", room=ROOM, title="t3b job", body="work", assignee_name="shadow-b")
    cl = await call(B, "task_claim", task_id=t["task_id"])
    out = await call(B, "task_block", task_id=t["task_id"], claim_token=cl["claim_token"],
                     reason="quota out", fail_class="quota")
    note("3 exhausted member skipped as candidate", out["relayed_to"] == "shadow-c", str(out))
    # dispatcher corrects a back to 25; c blocks: a (25) beats b (100)
    await call(DISP, "member_quota_set", member_name="shadow-a", week_used_pct=25)
    await call(A, "heartbeat")
    t = await call(DISP, "task_create", room=ROOM, title="t3c job", body="work", assignee_name="shadow-c")
    cl = await call(C, "task_claim", task_id=t["task_id"])
    out = await call(C, "task_block", task_id=t["task_id"], claim_token=cl["claim_token"],
                     reason="quota out", fail_class="quota")
    note("3 dispatcher correction restores candidacy", out["relayed_to"] == "shadow-a", str(out))

    # ---------- 4 sweep leaves healthy assignee alone ----------
    await call(A, "heartbeat")
    await call(DISP, "member_quota_set", member_name="shadow-a", week_used_pct=50)
    t = await call(DISP, "task_create", room=ROOM, title="t4 job", body="work", assignee_name="shadow-a")
    tid4 = t["task_id"]
    dbx("UPDATE tasks SET updated_at=? WHERE id=?", (time.time() - 2000, tid4))
    sweep(); sweep()
    row = taskrow(tid4)
    note("4 healthy assignee untouched past grace", row["status"] == "open"
         and row["assignee"] == member("shadow-a")["id"]
         and transfers(tid4) == [] and audit_n("task_unclaimed_escalate", tid4) == [])
    await call(DISP, "task_cancel", task_id=tid4)

    # ---------- 5 condition A: assignee offline ----------
    await call(B, "heartbeat")
    await call(DISP, "member_quota_set", member_name="shadow-b", week_used_pct=20)
    t = await call(DISP, "task_create", room=ROOM, title="t5a job", body="work", assignee_name="shadow-a")
    tid5 = t["task_id"]
    dbx("UPDATE members SET last_heartbeat=? WHERE name='shadow-a'", (time.time() - 400,))
    dbx("UPDATE tasks SET updated_at=? WHERE id=?", (time.time() - 1000, tid5))
    sweep()
    row = taskrow(tid5); tr = transfers(tid5)
    note("5A offline assignee relayed", row["status"] == "claimed"
         and row["assignee"] == member("shadow-b")["id"] and len(tr) == 1
         and tr[0]["from_member"] == "shadow-a" and tr[0]["to_member"] == "shadow-b"
         and tr[0]["actor"] == "auto-relay", f"status={row['status']} transfers={len(tr)}")
    # no candidate: b at 95 (fresh), c at 100 -> escalate exactly once
    await call(DISP, "member_quota_set", member_name="shadow-b", week_used_pct=95)
    t = await call(DISP, "task_create", room=ROOM, title="t5b job", body="work", assignee_name="shadow-a")
    tid5b = t["task_id"]
    dbx("UPDATE tasks SET updated_at=? WHERE id=?", (time.time() - 1000, tid5b))
    sweep(); sweep()
    row = taskrow(tid5b)
    msgs = dbq("SELECT * FROM messages WHERE task_id=? AND kind='chat' AND body LIKE '%未认领%'", (tid5b,))
    note("5A no candidate: stays open+assigned", row["status"] == "open"
         and row["assignee"] == member("shadow-a")["id"] and transfers(tid5b) == [])
    note("5A escalation exactly once over two sweeps", len(msgs) == 1
         and len(audit_n("task_unclaimed_escalate", tid5b)) == 1,
         f"msgs={len(msgs)} audits={len(audit_n('task_unclaimed_escalate', tid5b))}")
    await call(DISP, "task_cancel", task_id=tid5b)

    # ---------- 6 condition B: fresh quota >=90; stale quota never triggers ----------
    await call(A, "heartbeat")
    await call(DISP, "member_quota_set", member_name="shadow-a", week_used_pct=95)
    await call(B, "heartbeat")
    await call(DISP, "member_quota_set", member_name="shadow-b", week_used_pct=20)
    t = await call(DISP, "task_create", room=ROOM, title="t6a job", body="work", assignee_name="shadow-a")
    tid6 = t["task_id"]
    dbx("UPDATE tasks SET updated_at=? WHERE id=?", (time.time() - 1000, tid6))
    sweep()
    row = taskrow(tid6); tr = transfers(tid6)
    note("6B fresh 95pct assignee relayed", len(tr) == 1 and tr[0]["to_member"] == "shadow-b"
         and row["assignee"] == member("shadow-b")["id"])
    t = await call(DISP, "task_create", room=ROOM, title="t6b job", body="work", assignee_name="shadow-a")
    tid6b = t["task_id"]
    dbx("UPDATE members SET quota_week_used_pct=95, quota_updated_at=? WHERE name='shadow-a'",
        (time.time() - 901,))
    await call(A, "heartbeat")  # heartbeat fresh: only B could fire, and its data is stale
    dbx("UPDATE tasks SET updated_at=? WHERE id=?", (time.time() - 1000, tid6b))
    sweep()
    row = taskrow(tid6b)
    note("6B stale quota data never triggers", row["status"] == "open"
         and row["assignee"] == member("shadow-a")["id"] and transfers(tid6b) == []
         and audit_n("task_unclaimed_escalate", tid6b) == [])
    await call(DISP, "task_cancel", task_id=tid6b)

    # ---------- 7 pinned executor cards excluded ----------
    t = await call(DISP, "task_create", room=ROOM, title="[M4A] pinned job",
                   body="body mentions [M4A] too", assignee_name="shadow-a")
    tid7 = t["task_id"]
    dbx("UPDATE members SET last_heartbeat=? WHERE name='shadow-a'", (time.time() - 400,))
    dbx("UPDATE tasks SET updated_at=? WHERE id=?", (time.time() - 1000, tid7))
    sweep()
    row = taskrow(tid7)
    note("7 pinned card untouched under cond A", row["status"] == "open"
         and row["assignee"] == member("shadow-a")["id"] and transfers(tid7) == []
         and audit_n("task_unclaimed_escalate", tid7) == [])
    await call(DISP, "task_cancel", task_id=tid7)

    # ---------- 8 grace-period boundaries ----------
    t = await call(DISP, "task_create", room=ROOM, title="t8a job", body="work", assignee_name="shadow-a")
    tid8 = t["task_id"]
    dbx("UPDATE tasks SET updated_at=? WHERE id=?", (time.time() - 100, tid8))  # inside 240s grace
    sweep()
    row = taskrow(tid8)
    note("8 inside grace: no trigger", row["status"] == "open" and transfers(tid8) == [])
    await call(DISP, "task_cancel", task_id=tid8)
    # backoff card: eligible_at counts from next_claim_at, not updated_at
    t = await call(DISP, "task_create", room=ROOM, title="t8b job", body="work", assignee_name="shadow-a")
    tid8b = t["task_id"]
    dbx("UPDATE tasks SET updated_at=?, next_claim_at=? WHERE id=?",
        (time.time() - 5000, time.time() - 100, tid8b))
    sweep()
    row = taskrow(tid8b)
    note("8 backoff card: grace runs from next_claim_at", row["status"] == "open"
         and transfers(tid8b) == [] and audit_n("task_unclaimed_escalate", tid8b) == [])
    await call(B, "heartbeat")
    await call(DISP, "member_quota_set", member_name="shadow-b", week_used_pct=20)
    dbx("UPDATE tasks SET next_claim_at=? WHERE id=?", (time.time() - 1000, tid8b))
    sweep()
    row = taskrow(tid8b); tr = transfers(tid8b)
    note("8 backoff expired + grace passed: relayed", len(tr) == 1
         and tr[0]["to_member"] == "shadow-b" and row["assignee"] == member("shadow-b")["id"])

    print(f"\n{sum(res)}/{len(res)} passed")
    if not all(res):
        sys.exit(1)

asyncio.run(main())
