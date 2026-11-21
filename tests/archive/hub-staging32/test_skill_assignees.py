import asyncio, json, sys
sys.path.insert(0, ".")
from harness import call, setup_members, TOKENS, DISP

FAILS = []
def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond: FAILS.append(name)

async def main():
    await setup_members()
    alice = TOKENS["alice"]
    ok, r = await call(DISP, "skill_publish", name="skill-a", description="A", body="body a")
    check("publish A", ok, str(r)[:80])
    ok, r = await call(DISP, "skill_publish", name="skill-b", description="B", body="body b")
    check("publish B", ok, str(r)[:80])
    ok, r = await call(DISP, "skill_assign", name="skill-a", member_name="alice")
    check("assign A->alice", ok, str(r)[:80])

    ok, r = await call(DISP, "skill_list")
    skills = {s["name"]: s for s in r["skills"]}
    check("disp sees A+B", set(skills) == {"skill-a", "skill-b"}, str(list(skills)))
    check("A assignees alice/enabled", skills["skill-a"].get("assignees") == [{"member": "alice", "enabled": True}], str(skills["skill-a"].get("assignees")))
    check("B assignees empty", skills["skill-b"].get("assignees") == [], str(skills["skill-b"].get("assignees")))

    ok, r = await call(alice, "skill_list")
    names = [s["name"] for s in r["skills"]]
    check("alice sees only A", names == ["skill-a"], str(names))
    check("alice view has no assignees key", r["skills"] and "assignees" not in r["skills"][0], str(r["skills"]))

    ok, r = await call(DISP, "skill_assign", name="skill-b", member_name="alice")
    check("assign B->alice", ok, str(r)[:80])
    ok, r = await call(DISP, "skill_unassign", name="skill-a", member_name="alice")
    check("unassign A", ok, str(r)[:80])
    ok, r = await call(DISP, "skill_list")
    skills = {s["name"]: s for s in r["skills"]}
    a_asg = skills["skill-a"].get("assignees")
    check("A assignees after unassign (empty or disabled)", a_asg == [] or a_asg == [{"member": "alice", "enabled": False}], str(a_asg))
    check("B assignees alice", skills["skill-b"].get("assignees") == [{"member": "alice", "enabled": True}], str(skills["skill-b"].get("assignees")))
    ok, r = await call(alice, "skill_list")
    check("alice now sees only B", [s["name"] for s in r["skills"]] == ["skill-b"], str(r)[:120])

    print("TOTAL FAILS:", len(FAILS))
    return 1 if FAILS else 0

sys.exit(asyncio.run(main()))
