import asyncio, json, sys
sys.path.insert(0, ".")
from harness import call, call_err, client, DISP

res = []
def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)

async def main():
    # 0 tool count + new tool present
    async with client(DISP) as c:
        tools = await c.list_tools()
    names = [t.name for t in tools]
    note("0 tool count 64", len(names) == 64, f"got {len(names)}")
    note("0 member_login_state_set present", "member_login_state_set" in names)

    # 1 legacy register without provider: backward compatible
    r1 = await call(DISP, "member_register", name="legacy")
    note("1 legacy register ok", r1["name"] == "legacy" and r1.get("provider") is None
         and r1.get("login_state") is None)
    legacy_tok = r1["token"]

    # 2 register with provider
    r2 = await call(DISP, "member_register", name="newbie", provider="muse")
    note("2 provider register", r2.get("provider") == "muse" and r2.get("login_state") == "pending",
         f"got provider={r2.get('provider')} login_state={r2.get('login_state')}")
    newbie_tok = r2["token"]

    # 3 member_list carries the fields
    ml = await call(DISP, "member_list")
    by = {m["name"]: m for m in ml["members"]}
    note("3 list legacy fields empty", by["legacy"].get("provider") is None
         and by["legacy"].get("login_state") is None)
    note("3 list newbie fields", by["newbie"].get("provider") == "muse"
         and by["newbie"].get("login_state") == "pending")
    note("3 list old fields intact", "week_done" in by["newbie"] and "avatar" in by["newbie"])

    # 4 state transitions
    for st in ("logged_in", "expired", "pending", "logged_in"):
        out = await call(DISP, "member_login_state_set", name="newbie", state=st)
        assert out["login_state"] == st and out["provider"] == "muse", out
    ml = await call(DISP, "member_list")
    by = {m["name"]: m for m in ml["members"]}
    note("4 transitions land", by["newbie"]["login_state"] == "logged_in")

    # 5 bad states rejected
    for bad in ("hacked", "", "LOGGED_IN", "logged-in", "ok"):
        e = await call_err(DISP, "member_login_state_set", name="newbie", state=bad)
        assert e is not None, f"state {bad!r} was accepted"
    note("5 bad states rejected", True)

    # 6 nonexistent + removed member rejected
    e = await call_err(DISP, "member_login_state_set", name="ghost", state="pending")
    note("6 nonexistent rejected", e is not None)
    await call(DISP, "member_register", name="goner", provider="muse")
    await call(DISP, "member_remove", name="goner")
    e = await call_err(DISP, "member_login_state_set", name="goner", state="pending")
    note("6 removed rejected", e is not None)
    ml = await call(DISP, "member_list")
    note("6 removed hidden from list", "goner" not in {m["name"] for m in ml["members"]})

    # 7 authz: member token cannot set state or register with provider
    e = await call_err(newbie_tok, "member_login_state_set", name="newbie", state="expired")
    note("7 member cannot set own state", e is not None)
    e = await call_err(legacy_tok, "member_login_state_set", name="legacy", state="pending")
    note("7 member cannot set state (legacy)", e is not None)
    e = await call_err(newbie_tok, "member_register", name="sneaky", provider="muse")
    note("7 member cannot register", e is not None)
    ml = await call(DISP, "member_list")
    by = {m["name"]: m for m in ml["members"]}
    note("7 state unchanged after attacks", by["newbie"]["login_state"] == "logged_in")

    # 8 provider too long rejected
    e = await call_err(DISP, "member_register", name="longprov", provider="x" * 65)
    note("8 provider >64 rejected", e is not None)

    # 9 set state on provider-less member is allowed (state tracked independently)
    out = await call(DISP, "member_login_state_set", name="legacy", state="pending")
    note("9 provider-less member state set", out["login_state"] == "pending"
         and out.get("provider") is None)

    print(f"\n{sum(res)}/{len(res)} passed")
    if not all(res):
        sys.exit(1)

asyncio.run(main())
