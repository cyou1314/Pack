"""staging28 定向测试：register 指定头像组合 + member_remove 软删全链。"""
import asyncio, json, sys
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging28")
from harness import call, setup_members, TOKENS, DISP

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")

async def main():
    await setup_members()
    D = TOKENS["dispatcher"]
    A = TOKENS["alice"]

    # --- register 指定组合 ---
    ok, r = await call(D, "member_register", name="carol",
                       avatar_combo=json.dumps({"species": "wolf", "palette": "teal",
                                                "accessory": "scarf", "bg": "sand"}))
    check("register full combo ok", ok and r["avatar"]["combo"] == "wolf|teal|scarf|sand", str(r))
    carol = r["token"]
    ok, r = await call(D, "member_register", name="dave",
                       avatar_combo=json.dumps({"species": "cat"}))
    check("register partial combo keeps species", ok and r["avatar"]["species"] == "cat", str(r))
    ok, r = await call(D, "member_register", name="erin",
                       avatar_combo=json.dumps({"species": "dragon"}))
    check("bad species rejected", not ok and "bad species" in str(r), str(r))
    ok, r = await call(D, "member_register", name="erin", avatar_combo="{oops")
    check("bad JSON rejected", not ok and "not valid JSON" in str(r), str(r))
    ok, r = await call(D, "member_register", name="erin", avatar_combo="[1,2]")
    check("non-object JSON rejected", not ok and "must be a JSON object" in str(r), str(r))
    ok, r = await call(D, "member_register", name="erin",
                       avatar_combo=json.dumps({"species": "wolf", "palette": "teal",
                                                "accessory": "scarf", "bg": "sand"}))
    check("taken combo rejected", not ok and "already taken" in str(r), str(r))
    ok, r = await call(D, "member_register", name="carol")
    check("duplicate active name rejected", not ok and "already exists" in str(r), str(r))
    ok, r = await call(A, "member_register", name="mallory")
    check("member cannot register", not ok, str(r))

    # 并发抢同一组合：恰一成一败
    combo = json.dumps({"species": "deer", "palette": "rose", "accessory": "cap", "bg": "sky"})
    res = await asyncio.gather(
        call(D, "member_register", name="fred", avatar_combo=combo),
        call(D, "member_register", name="gina", avatar_combo=combo))
    check("concurrent same-combo register exactly one wins",
          sum(1 for ok, _ in res if ok) == 1, str(res))

    # --- 给 carol 布置移除现场 ---
    ok, t1 = await call(D, "task_create", room="pack-main", title="carol 的进行中任务", body="drill",
                        assignee_name="carol")
    t1 = t1["task_id"]
    ok, cl = await call(carol, "task_claim", task_id=t1)
    check("carol claims task", ok and cl.get("claim_token"), str(cl))
    ok, acq = await call(carol, "desktop_acquire", task_id=t1, claim_token=cl["claim_token"])
    check("carol holds desktop lock", ok and acq.get("held"), str(acq))
    ok, t2 = await call(D, "task_create", room="pack-main", title="carol 的待认领任务", body="drill",
                        assignee_name="carol")
    t2 = t2["task_id"]
    ok, t3 = await call(D, "task_create", room="pack-main", title="转交目标任务", body="drill",
                        assignee_name="alice")
    t3 = t3["task_id"]
    ok, cl3 = await call(A, "task_claim", task_id=t3)
    ok, tr = await call(A, "task_transfer", task_id=t3, to_member="carol",
                        claim_token=cl3["claim_token"], reason="test")
    check("pending transfer to carol created", ok, str(tr))
    ok, sk = await call(D, "skill_publish", name="s28-probe", description="probe",
                        body="probe body")
    ok, sa = await call(D, "skill_assign", name="s28-probe", member_name="carol")
    check("skill assigned to carol", ok, str(sa))

    # --- remove 的拒绝面 ---
    ok, r = await call(A, "member_remove", name="carol")
    check("member cannot remove", not ok, str(r))
    ok, r = await call(D, "member_remove", name="nobody")
    check("remove nonexistent refused", not ok and "no such member" in str(r), str(r))
    ok, r = await call(D, "member_remove", name="dispatcher")
    check("remove dispatcher refused", not ok, str(r))
    ok, r = await call(D, "member_remove", name="owner")
    check("remove owner refused", not ok, str(r))

    # 并发双移除：恰一次成功
    res = await asyncio.gather(call(D, "member_remove", name="carol"),
                                 call(D, "member_remove", name="carol"))
    wins = [r for ok, r in res if ok]
    check("concurrent double remove exactly one wins", len(wins) == 1, str(res))
    if wins:
        w = wins[0]
        check("remove summary counts", w["tasks_recalled"] == 2 and w["tasks_unassigned"] == 1
              and w["transfers_expired"] == 1 and w["skills_cleared"] == 1
              and w["lock_released"] in (True, False), str(w))

    # --- 移除后状态 ---
    ok, r = await call(carol, "member_list")
    check("removed member token revoked", not ok, str(r))
    ok, lst = await call(D, "member_list")
    names = [m["name"] for m in lst["members"]]
    check("removed member hidden from list", "carol" not in names and "dave" in names, str(names))
    ok, tasks = await call(D, "task_list")
    by_id = {t["id"]: t for t in tasks["tasks"]}
    check("claimed task recalled to open+unassigned",
          by_id[t1]["status"] == "open" and by_id[t1]["assignee"] is None, str(by_id.get(t1)))
    check("open task unassigned", by_id[t2]["assignee"] is None, str(by_id.get(t2)))
    ok, st = await call(D, "desktop_status")
    check("desktop lock free after remove", not st["held"], str(st))
    ok, trs = await call(D, "task_transfers_list", task_id=t3)
    check("pending transfer expired",
          any(x["status"] == "expired" for x in trs["transfers"]), str(trs))
    check("limbo task requeued to open+unassigned",
          by_id[t3]["status"] == "open" and by_id[t3]["assignee"] is None, str(by_id.get(t3)))
    ok, r = await call(D, "member_remove", name="carol")
    check("double remove refused", not ok and "already removed" in str(r), str(r))
    ok, r = await call(D, "member_register", name="carol")
    check("removed name stays reserved", not ok and "reserved" in str(r), str(r))
    ok, r = await call(D, "member_register", name="heidi",
                       avatar_combo=json.dumps({"species": "wolf", "palette": "teal",
                                                "accessory": "scarf", "bg": "sand"}))
    check("removed member combo freed for reuse", ok and r["avatar"]["combo"] == "wolf|teal|scarf|sand", str(r))

    print(f"\nRESULT pass={PASS} fail={FAIL}")
    return FAIL

if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
