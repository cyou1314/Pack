#!/usr/bin/env python3
"""staging37: RT1 requeue keeps assignee (release / review-reject / lease-sweep /
transfer-expired), removed-member guard falls back to unassigned.
Run: start staging37 hub (port 8837), then ../hub/.venv/bin/python test_rt1.py
"""
import asyncio, sqlite3, subprocess, sys
sys.path.insert(0, ".")
from harness import call, setup_members, TOKENS

DB = "/home/hatch/workspace/pack/hub-staging37/pack.db"
PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {extra}")


def db_exec(sql, args=()):
    c = sqlite3.connect(DB)
    c.execute(sql, args)
    c.commit()
    c.close()


def db_one(sql, args=()):
    c = sqlite3.connect(DB)
    r = c.execute(sql, args).fetchone()
    c.close()
    return r


def sweep():
    r = subprocess.run([sys.executable, "-c",
                        "import hub; hub.sweep_once()"],
                       cwd="/home/hatch/workspace/pack/hub-staging37",
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]


async def open_ids(tok, name):
    ok, lst = await call(tok, "task_list", status="open", assignee_name=name)
    return [t["id"] for t in lst["tasks"]] if ok else []


async def main():
    await setup_members()
    disp, alice, bob = TOKENS["dispatcher"], TOKENS["alice"], TOKENS["bob"]

    # --- 1) task_release preserves assignee ---
    ok, t = await call(disp, "task_create", room="pack-main", title="rt1 release", body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, cl = await call(alice, "task_claim", task_id=tid)
    check("1 claim by alice", ok, str(cl))
    ok, bl = await call(alice, "task_block", task_id=tid, claim_token=cl["claim_token"], reason="stuck", fail_class="transient")
    check("1 block", ok and bl.get("status") == "blocked", str(bl))
    ok, rel = await call(disp, "task_release", task_id=tid, clear_backoff=True)
    check("1 release ok", ok and rel.get("status") == "open", str(rel))
    check("1 released task visible to alice by-name poll", tid in await open_ids(alice, "alice"))
    check("1 released task NOT visible to bob by-name poll", tid not in await open_ids(bob, "bob"))
    ok, _ = await call(bob, "task_claim", task_id=tid)
    check("1 bob cannot claim alice's released task", not ok)
    ok, cl2 = await call(alice, "task_claim", task_id=tid)
    check("1 alice re-claims released task", ok, str(cl2))
    await call(alice, "task_block", task_id=tid, claim_token=cl2["claim_token"], reason="park", fail_class="task")
    await call(disp, "task_cancel", task_id=tid)

    # --- 2) task_review reject preserves assignee ---
    ok, t = await call(disp, "task_create", room="pack-main", title="rt1 review", body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, cl = await call(alice, "task_claim", task_id=tid)
    ok, cp = await call(alice, "task_complete", task_id=tid, claim_token=cl["claim_token"], result="v1")
    check("2 complete -> review", ok and cp.get("status") == "review", str(cp))
    ok, rv = await call(disp, "task_review", task_id=tid, approve=False, note="redo")
    check("2 reject -> open", ok and rv.get("status") == "open", str(rv))
    check("2 rejected task visible to alice by-name poll", tid in await open_ids(alice, "alice"))
    check("2 rejected task NOT visible to bob", tid not in await open_ids(bob, "bob"))
    db_exec("UPDATE tasks SET next_claim_at=NULL WHERE id=?", (tid,))
    ok, _ = await call(alice, "task_claim", task_id=tid)
    check("2 alice re-claims after backoff cleared", ok)
    await call(disp, "task_cancel", task_id=tid)

    # --- 3) lease-expiry sweep preserves assignee ---
    ok, t = await call(disp, "task_create", room="pack-main", title="rt1 lease", body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, cl = await call(alice, "task_claim", task_id=tid)
    check("3 claim", ok, str(cl))
    db_exec("UPDATE tasks SET lease_expires=1 WHERE id=?", (tid,))
    sweep()
    row = db_one("SELECT status, assignee FROM tasks WHERE id=?", (tid,))
    alice_id = db_one("SELECT id FROM members WHERE name='alice'")[0]
    check("3 swept back to open", row and row[0] == "open", str(row))
    check("3 assignee preserved after sweep", row and row[1] == alice_id, str(row))
    check("3 swept task visible to alice by-name poll", tid in await open_ids(alice, "alice"))
    db_exec("UPDATE tasks SET next_claim_at=NULL WHERE id=?", (tid,))
    ok, _ = await call(alice, "task_claim", task_id=tid)
    check("3 alice re-claims swept task", ok)
    await call(disp, "task_cancel", task_id=tid)

    # --- 4) transfer-expired returns to sender ---
    ok, t = await call(disp, "task_create", room="pack-main", title="rt1 transfer", body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, cl = await call(alice, "task_claim", task_id=tid)
    ok, tr = await call(alice, "task_transfer", task_id=tid, claim_token=cl["claim_token"], to_member="bob", reason="try")
    check("4 transfer created", ok, str(tr))
    db_exec("UPDATE task_transfers SET expires_at=1 WHERE task_id=? AND status='pending'", (tid,))
    sweep()
    row = db_one("SELECT status, assignee FROM tasks WHERE id=?", (tid,))
    check("4 expired transfer requeued open", row and row[0] == "open", str(row))
    check("4 assignee back to sender alice", row and row[1] == alice_id, str(row))
    ok, _ = await call(alice, "task_claim", task_id=tid)
    check("4 alice re-claims returned task", ok)
    await call(disp, "task_cancel", task_id=tid)

    # --- 5) removed-member guard: release falls back to unassigned ---
    ok, t = await call(disp, "task_create", room="pack-main", title="rt1 removed", body="b", assignee_name="bob")
    tid = t["task_id"]
    ok, cl = await call(bob, "task_claim", task_id=tid)
    ok, bl = await call(bob, "task_block", task_id=tid, claim_token=cl["claim_token"], reason="stuck", fail_class="transient")
    check("5 bob blocked his task", ok and bl.get("status") == "blocked", str(bl))
    ok, rm = await call(disp, "member_remove", name="bob")
    check("5 bob removed", ok, str(rm))
    row = db_one("SELECT status, assignee FROM tasks WHERE id=?", (tid,))
    print("   after remove:", row)
    if row and row[0] == "blocked":
        ok, rel = await call(disp, "task_release", task_id=tid, clear_backoff=True)
        check("5 release of removed member's task ok", ok, str(rel))
        row = db_one("SELECT status, assignee FROM tasks WHERE id=?", (tid,))
        check("5 assignee NULL after release (guard)", row and row[1] is None, str(row))
        ok, _ = await call(alice, "task_claim", task_id=tid)
        check("5 alice can claim the unassigned task", ok)
        await call(disp, "task_cancel", task_id=tid)
    else:
        check("5 blocked task survived removal as blocked", False, str(row))

    # --- 6) unassigned pool still claimable by anyone ---
    ok, t = await call(disp, "task_create", room="pack-main", title="rt1 pool", body="b")
    tid = t["task_id"]
    ok, _ = await call(alice, "task_claim", task_id=tid)
    check("6 unassigned task claimable", ok)
    await call(disp, "task_cancel", task_id=tid)

    print(f"RESULT pass={PASS} fail={FAIL}")

asyncio.run(main())
