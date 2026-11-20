import asyncio, os, re, sqlite3, subprocess, sys, time
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

def member(name):
    rows = dbq("SELECT * FROM members WHERE name=?", (name,))
    return rows[0] if rows else None

def server_pid():
    out = subprocess.run(["ss", "-ltnp", "sport = :18796"],
                         capture_output=True, text=True).stdout
    m = re.search(r"pid=(\d+)", out)
    assert m, f"no staging server on 18796: {out}"
    return int(m.group(1))

def db_fd_count(pid):
    n = 0
    fd_dir = f"/proc/{pid}/fd"
    for fd in os.listdir(fd_dir):
        try:
            tgt = os.readlink(os.path.join(fd_dir, fd))
        except OSError:
            continue
        if tgt.startswith(DB):
            n += 1
    return n

def fd_floor(pid, samples=3):
    return min(db_fd_count(pid) for _ in range(samples))

EXPECTED_MEMBER_KEYS = {
    "id", "name", "role", "profile", "created_at", "last_heartbeat", "avatar",
    "quota_week_used_pct", "quota_week_reset_at", "quota_extra_left",
    "quota_updated_at", "quota_source", "provider", "login_state", "week_done",
}

async def main():
    # ---------- 0 tool surface ----------
    async with client(DISP) as c:
        tools = await c.list_tools()
    names = [t.name for t in tools]
    note("0 tool count 65", len(names) == 65, f"got {len(names)}")
    tl = [t for t in tools if t.name == "task_list"][0]
    props = (tl.inputSchema or {}).get("properties", {})
    note("0 task_list schema has room param", "room" in props,
         f"props={sorted(props)}")

    # ---------- 1 member_list: no fd growth, structure unchanged ----------
    pid = server_pid()
    for _ in range(5):
        await call(DISP, "member_list")
    base = fd_floor(pid)
    out = await call(DISP, "member_list")
    keys_ok = all(set(m.keys()) == EXPECTED_MEMBER_KEYS for m in out["members"])
    # fresh staging DB seeds only the dispatcher (owner is not an init seed),
    # so >=1 member is the right floor here; the field set is the real check.
    note("1 member_list field set unchanged", keys_ok and len(out["members"]) >= 1,
         f"members={len(out['members'])}")
    ok_all = True
    for _ in range(50):
        r = await call(DISP, "member_list")
        ok_all = ok_all and "members" in r
    after = fd_floor(pid)
    note("1 50x member_list all succeed", ok_all)
    note("1 db fds do not grow over 50 calls", after <= base,
         f"pid={pid} baseline={base} after={after}")

    # ---------- 2 removed member rejected on all write paths ----------
    rg = await call(DISP, "member_register", name="gone49")
    rp = await call(DISP, "member_register", name="pal49")
    rh = await call(DISP, "member_register", name="holder49")
    PAL, HOLD = rp["token"], rh["token"]
    await call(DISP, "skill_publish", name="s49", body="skill body 49")
    await call(DISP, "template_upsert", name="tpl49", title_tpl="T49 job",
               body_tpl="body", assignee="gone49")
    await call(DISP, "member_quota_set", member_name="gone49", week_used_pct=33)
    # transfer guinea pig: task held by holder49
    t0 = await call(DISP, "task_create", room=ROOM, title="t2 held job",
                    body="work", assignee_name="holder49")
    cl = await call(HOLD, "task_claim", task_id=t0["task_id"])
    snap_tasks = len(dbq("SELECT id FROM tasks"))
    snap_transfers = len(dbq("SELECT id FROM task_transfers"))
    snap_templates = sorted(r["name"] for r in
                            dbq("SELECT name FROM task_templates"))
    snap_quota = member("gone49")
    rm = await call(DISP, "member_remove", name="gone49")
    note("2 setup: gone49 removed", rm["removed"] == "gone49")

    attempts = []
    e = await call_err(DISP, "task_create", room=ROOM, title="x", body="x",
                       assignee_name="gone49")
    attempts.append(("task_create", e))
    e = await call_err(DISP, "task_schedule", room=ROOM, title="x", body="x",
                       member_name="gone49", at_epoch=time.time() + 7200)
    attempts.append(("task_schedule", e))
    e = await call_err(DISP, "template_upsert", name="tpl49b", title_tpl="t",
                       body_tpl="b", assignee="gone49")
    attempts.append(("template_upsert", e))
    e = await call_err(DISP, "template_fire", name="tpl49")
    attempts.append(("template_fire", e))
    e = await call_err(DISP, "member_quota_set", member_name="gone49",
                       week_used_pct=55)
    attempts.append(("member_quota_set", e))
    e = await call_err(HOLD, "task_transfer", task_id=t0["task_id"],
                       claim_token=cl["claim_token"], to_member="gone49")
    attempts.append(("task_transfer", e))
    e = await call_err(DISP, "skill_assign", name="s49", member_name="gone49")
    attempts.append(("skill_assign", e))
    for label, e in attempts:
        note(f"2 {label} rejects removed member",
             e is not None and "member is removed" in e and "gone49" in e,
             (e or "NO ERROR")[:110])
    note("2 no new tasks from rejected writes",
         len(dbq("SELECT id FROM tasks")) == snap_tasks,
         f"tasks {snap_tasks} -> {len(dbq('SELECT id FROM tasks'))}")
    note("2 no new transfers", len(dbq("SELECT id FROM task_transfers")) == snap_transfers)
    note("2 templates unchanged",
         sorted(r["name"] for r in dbq("SELECT name FROM task_templates")) == snap_templates)
    g = member("gone49")
    note("2 removed member quota untouched",
         g["quota_week_used_pct"] == snap_quota["quota_week_used_pct"]
         and g["quota_updated_at"] == snap_quota["quota_updated_at"])
    note("2 no skill rows for removed member",
         dbq("SELECT * FROM member_skills WHERE member_id=?", (g["id"],)) == [])
    trow = dbq("SELECT * FROM tasks WHERE id=?", (t0["task_id"],))[0]
    note("2 transfer target task untouched",
         trow["assignee"] == member("holder49")["id"] and trow["status"] == "claimed")
    e = await call_err(DISP, "task_create", room=ROOM, title="x", body="x",
                       assignee_name="ghost49")
    note("2 nonexistent member keeps no-such-member error",
         e is not None and "no such member" in e, (e or "NO ERROR")[:110])

    # control group: in-register member pal49 passes every path
    t = await call(DISP, "task_create", room=ROOM, title="c job", body="work",
                   assignee_name="pal49")
    note("2 control task_create ok", t["assignee"] == "pal49")
    t = await call(DISP, "task_schedule", room=ROOM, title="c sched", body="work",
                   member_name="pal49", at_epoch=time.time() + 7200)
    note("2 control task_schedule ok", t["assignee"] == "pal49")
    await call(DISP, "template_upsert", name="tpl49c", title_tpl="TC job",
               body_tpl="body", assignee="pal49")
    out = await call(DISP, "template_fire", name="tpl49c")
    note("2 control template_upsert+fire ok", out["assignee"] == "pal49")
    out = await call(DISP, "member_quota_set", member_name="pal49", week_used_pct=44)
    note("2 control quota_set ok", out["quota_week_used_pct"] == 44)
    t2 = await call(DISP, "task_create", room=ROOM, title="c held", body="work",
                    assignee_name="holder49")
    cl2 = await call(HOLD, "task_claim", task_id=t2["task_id"])
    out = await call(HOLD, "task_transfer", task_id=t2["task_id"],
                     claim_token=cl2["claim_token"], to_member="pal49")
    note("2 control transfer ok", out["transfer_status"] == "pending"
         and out["to_member"] == "pal49")
    out = await call(DISP, "skill_assign", name="s49", member_name="pal49")
    note("2 control skill_assign ok", out["enabled"] is True)

    # ---------- 3 task_list room field + filter ----------
    await call(DISP, "room_create", name="room-b49")
    a = await call(DISP, "task_create", room=ROOM, title="r49 main plain", body="w")
    b = await call(DISP, "task_create", room=ROOM, title="r49 main assigned",
                   body="w", assignee_name="pal49")
    c49 = await call(DISP, "task_create", room="room-b49", title="r49 b job", body="w")
    allrows = (await call(DISP, "task_list"))["tasks"]
    byid = {r["id"]: r for r in allrows}
    note("3 every row carries room", all(r.get("room") for r in allrows),
         f"rows={len(allrows)}")
    note("3 room values correct",
         byid[a["task_id"]]["room"] == ROOM and byid[b["task_id"]]["room"] == ROOM
         and byid[c49["task_id"]]["room"] == "room-b49")
    ids = [r["id"] for r in allrows]
    note("3 ordering still id asc (no urgent set)", ids == sorted(ids))
    frows = (await call(DISP, "task_list", room="room-b49"))["tasks"]
    note("3 room filter returns only that room",
         [r["id"] for r in frows] == [c49["task_id"]],
         f"got {[r['id'] for r in frows]}")
    frows = (await call(DISP, "task_list", room=ROOM))["tasks"]
    fids = {r["id"] for r in frows}
    note("3 room=pack-main includes main tasks, excludes b-room",
         a["task_id"] in fids and b["task_id"] in fids
         and c49["task_id"] not in fids)
    frows = (await call(DISP, "task_list", room="no-such-room-49"))["tasks"]
    note("3 unknown room -> empty list, no error", frows == [])
    srows = (await call(DISP, "task_list", status="open"))["tasks"]
    drows = (await call(DISP, "task_list", status="done"))["tasks"]
    note("3 status filter unchanged",
         a["task_id"] in {r["id"] for r in srows}
         and a["task_id"] not in {r["id"] for r in drows})
    arows = (await call(DISP, "task_list", assignee_name="pal49"))["tasks"]
    aids = {r["id"] for r in arows}
    note("3 assignee filter unchanged",
         b["task_id"] in aids and a["task_id"] not in aids)
    orows = (await call(DISP, "task_list", offset=2))["tasks"]
    note("3 offset shifts the same sequence",
         [r["id"] for r in orows] == ids[2:],
         f"full={len(ids)} shifted={len(orows)}")

    print(f"\n{sum(res)}/{len(res)} passed")
    if not all(res):
        sys.exit(1)

asyncio.run(main())
