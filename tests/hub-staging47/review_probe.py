import asyncio, sqlite3, sys
sys.path.insert(0, ".")
from harness import call, call_err, DISP

res = []
def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)

async def main():
    # setup: one shared member + a member token for forgery probes
    shared = await call(DISP, "member_register", name="probe-shared", provider="muse")
    mtok = shared["token"]

    # P1: 10 parallel state sets on the SAME member — all succeed, audit == successes,
    # final state is one of the written states (no torn write)
    states = ["pending", "logged_in", "expired"] * 3 + ["logged_in"]
    results = await asyncio.gather(*[
        call(DISP, "member_login_state_set", name="probe-shared", state=s) for s in states
    ], return_exceptions=True)
    ok_count = sum(1 for r in results if isinstance(r, dict) and r.get("login_state") in states)
    note("P1 parallel same-member sets all succeed", ok_count == 10, f"ok={ok_count}/10")
    conn = sqlite3.connect("pack.db")
    n_audit = conn.execute(
        "SELECT COUNT(*) FROM audit WHERE action='member_login_state_set' AND detail LIKE 'probe-shared:%'"
    ).fetchone()[0]
    note("P1 audit rows == successful sets", n_audit == 10, f"audit={n_audit}")
    ml = await call(DISP, "member_list")
    final = {m["name"]: m for m in ml["members"]}["probe-shared"]["login_state"]
    note("P1 final state is a written state", final in states, f"final={final}")

    # P2: 8 parallel registers with the SAME name — exactly one wins
    results = await asyncio.gather(*[
        call(DISP, "member_register", name="probe-race", provider="muse") for _ in range(8)
    ], return_exceptions=True)
    wins = sum(1 for r in results if isinstance(r, dict) and r.get("name") == "probe-race")
    note("P2 same-name parallel register: exactly 1 wins", wins == 1, f"wins={wins}")

    # P3: 20 members x 10 sets each, members in parallel — each member's final == its last write
    async def member_run(i):
        nm = f"probe-m{i}"
        await call(DISP, "member_register", name=nm, provider="muse")
        last = None
        for j in range(10):
            last = ["pending", "logged_in", "expired"][j % 3]
            await call(DISP, "member_login_state_set", name=nm, state=last)
        return nm, last
    pairs = await asyncio.gather(*[member_run(i) for i in range(20)])
    ml = await call(DISP, "member_list")
    by = {m["name"]: m for m in ml["members"]}
    good = sum(1 for nm, last in pairs if by[nm]["login_state"] == last)
    note("P3 20 members x10 sets: finals consistent", good == 20, f"{good}/20")

    # P4: forgery — member token tries both write paths under the new surface
    e1 = await call_err(mtok, "member_login_state_set", name="probe-shared", state="expired")
    e2 = await call_err(mtok, "member_register", name="probe-forged", provider="muse")
    note("P4 member token refused on both writes", e1 is not None and e2 is not None)
    ml = await call(DISP, "member_list")
    names = {m["name"] for m in ml["members"]}
    note("P4 no forged member created", "probe-forged" not in names)
    cur = {m["name"]: m for m in ml["members"]}["probe-shared"]["login_state"]
    note("P4 shared state untouched by forgery", cur == final, f"cur={cur}")

    print(f"\n{sum(res)}/{len(res)} probes passed")
    if not all(res):
        sys.exit(1)

asyncio.run(main())
