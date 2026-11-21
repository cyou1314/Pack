import asyncio, os, sqlite3, sys, time
sys.path.insert(0, ".")
from harness import call, call_err, DISP

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

def member(name):
    return dbq("SELECT * FROM members WHERE name=?", (name,))[0]

def audit_n(action, actor):
    return dbq("SELECT * FROM audit WHERE action=? AND actor=?", (action, actor))

async def main():
    now = time.time()
    rd = await call(DISP, "member_register", name="shadow-d")
    re_ = await call(DISP, "member_register", name="shadow-e")
    D, E = rd["token"], re_["token"]

    # ---- 1 same-cycle drop blocked, db unchanged, audit trail ----
    out = await call(D, "member_quota_report", week_used_pct=50, week_reset_at=now + 3 * 86400)
    assert out["quota_week_used_pct"] == 50 and out["quota_source"] == "self", out
    before = member("shadow-d")
    e = await call_err(D, "member_quota_report", week_used_pct=30)
    after = member("shadow-d")
    note("A1 same-cycle drop rejected", e is not None)
    note("A1 db unchanged after drop attempt",
         after["quota_week_used_pct"] == 50
         and after["quota_updated_at"] == before["quota_updated_at"])
    note("A1 drop-blocked audit written", len(audit_n("member_quota_drop_blocked", "shadow-d")) == 1)

    # ---- 2 fake future reset_at cannot wash ----
    out = await call(D, "member_quota_report", week_used_pct=10, week_reset_at=now + 10 * 86400)
    note("A2 future reset_at wash: pct drop blocked", out["quota_week_used_pct"] == 50
         and "week_used_pct" in out.get("blocked_fields", []), str(out.get("blocked_fields")))
    stored_reset = member("shadow-d")["quota_week_reset_at"]
    e = await call_err(D, "member_quota_report", week_reset_at=now + 1 * 86400)
    note("A2 reset move-back blocked", e is not None
         and member("shadow-d")["quota_week_reset_at"] == stored_reset
         and len(audit_n("member_quota_reset_blocked", "shadow-d")) == 1)

    # ---- 3 genuine rollover: stored reset passed + newer reset reported ----
    dbx("UPDATE members SET quota_week_reset_at=? WHERE name='shadow-d'", (time.time() - 100,))
    out = await call(D, "member_quota_report", week_used_pct=10, week_reset_at=time.time() + 7 * 86400)
    note("A3 genuine rollover allows drop", out["quota_week_used_pct"] == 10
         and out["quota_week_reset_at"] > time.time() + 6 * 86400)

    # ---- 4 fact fuse: block quota pins 100; self-report cannot lower; dispatcher releases ----
    t = await call(DISP, "task_create", room=ROOM, title="a4 job", body="work", assignee_name="shadow-e")
    cl = await call(E, "task_claim", task_id=t["task_id"])
    out = await call(E, "task_block", task_id=t["task_id"], claim_token=cl["claim_token"],
                     reason="quota out", fail_class="quota")
    m = member("shadow-e")
    note("A4 fuse pins 100 with source=fact", m["quota_week_used_pct"] == 100
         and m["quota_source"] == "fact", f"pct={m['quota_week_used_pct']} src={m['quota_source']}")
    e = await call_err(E, "member_quota_report", week_used_pct=20)
    note("A4 fused self-report drop ineffective", e is not None
         and member("shadow-e")["quota_week_used_pct"] == 100)
    await call(DISP, "member_quota_set", member_name="shadow-e", week_used_pct=20)
    m = member("shadow-e")
    note("A4 dispatcher set releases fuse", m["quota_week_used_pct"] == 20
         and m["quota_source"] == "dispatcher")
    e = await call_err(E, "member_quota_report", week_used_pct=10)
    note("A4 self-report cannot lower dispatcher value", e is not None
         and member("shadow-e")["quota_week_used_pct"] == 20
         and len(audit_n("member_quota_drop_blocked", "shadow-e")) >= 1)

    # ---- 5 anomaly: +35 with no completion alerts; +35 with completion does not ----
    out = await call(D, "member_quota_report", week_used_pct=45)  # 10 -> 45, no task_complete yet
    note("A5 big jump accepted (report-high passes)", out["quota_week_used_pct"] == 45)
    note("A5 anomaly audit on jump without completion",
         len(audit_n("quota_anomaly", "shadow-d")) == 1)
    t = await call(DISP, "task_create", room=ROOM, title="a5 job", body="work", assignee_name="shadow-d")
    cl = await call(D, "task_claim", task_id=t["task_id"])
    await call(D, "task_complete", task_id=t["task_id"], claim_token=cl["claim_token"],
               result="done")
    out = await call(D, "member_quota_report", week_used_pct=80)  # 45 -> 80, completion in window
    note("A5 jump with completion in window: no anomaly",
         out["quota_week_used_pct"] == 80 and len(audit_n("quota_anomaly", "shadow-d")) == 1)

    # ---- 6 small rise accepted quietly ----
    out = await call(D, "member_quota_report", week_used_pct=85)
    note("A6 small rise accepted, no anomaly", out["quota_week_used_pct"] == 85
         and len(audit_n("quota_anomaly", "shadow-d")) == 1
         and member("shadow-d")["quota_source"] == "self")

    print(f"\n{sum(res)}/{len(res)} passed")
    if not all(res):
        sys.exit(1)

asyncio.run(main())
