"""staging16 专测：H1 额度 / H2 房间改名归档 / H3 头像组合 + 并发注册避撞 + 回归。"""
import asyncio, sys, time
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging16")
from harness import call, setup_members, TOKENS

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok {name}")
    else:
        FAIL += 1; print(f"  FAIL {name} {extra}")

async def main():
    toks = await setup_members()
    D, A, B = toks["dispatcher"], toks["alice"], toks["bob"]

    print("== H1 额度 ==")
    ok, out = await call(D, "member_quota_set", member_name="alice",
                         week_used_pct=62.0, week_reset_at=1764000000.0,
                         extra_tokens_left=436000000)
    check("set ok", ok and out["quota_week_used_pct"] == 62.0, str(out))
    check("updated_at set", ok and out["quota_updated_at"] > time.time() - 60)
    ok, out = await call(D, "member_list")
    alice = [m for m in out["members"] if m["name"] == "alice"][0]
    bob = [m for m in out["members"] if m["name"] == "bob"][0]
    check("list carries quota", alice["quota_extra_left"] == 436000000 and alice["quota_week_reset_at"] == 1764000000.0)
    check("unset member null", bob["quota_week_used_pct"] is None and bob["quota_updated_at"] is None)
    ok, out = await call(D, "member_quota_set", member_name="alice", week_used_pct=70.5)
    check("partial update", ok and out["quota_week_used_pct"] == 70.5 and out["quota_extra_left"] == 436000000, str(out))
    for kw, nm in [({"week_used_pct": 101.0}, "pct>100"), ({"week_used_pct": -1.0}, "pct<0"),
                   ({"extra_tokens_left": -5}, "extra<0"), ({"week_reset_at": 0.0}, "reset=0"),
                   ({}, "no fields")]:
        ok, out = await call(D, "member_quota_set", member_name="alice", **kw)
        check(f"reject {nm}", not ok, str(out))
    ok, out = await call(D, "member_quota_set", member_name="ghost", week_used_pct=1.0)
    check("reject unknown member", not ok)
    ok, out = await call(A, "member_quota_set", member_name="alice", week_used_pct=1.0)
    check("member cannot set quota", not ok)

    print("== H2 房间改名/归档 ==")
    ok, out = await call(D, "room_create", name="test-room")
    check("create", ok, str(out))
    ok, out = await call(D, "room_rename", room="test-room", new_name="renamed-room")
    check("rename", ok and out["name"] == "renamed-room", str(out))
    ok, out = await call(D, "room_list")
    names = [r["name"] for r in out["rooms"]]
    check("list shows new name", "renamed-room" in names and "test-room" not in names, str(names))
    check("archived flag present", all("archived" in r for r in out["rooms"]))
    ok, out = await call(D, "room_rename", room="renamed-room", new_name="pack-main")
    check("rename to existing rejected", not ok)
    ok, out = await call(D, "room_rename", room="pack-main", new_name="x-room")
    check("rename main rejected", not ok)
    ok, out = await call(D, "room_rename", room="ghost-room", new_name="y-room")
    check("rename ghost rejected", not ok)
    ok, out = await call(D, "room_rename", room="renamed-room", new_name="has space")
    check("rename invalid name rejected", not ok)
    ok, out = await call(A, "room_rename", room="renamed-room", new_name="z-room")
    check("member cannot rename", not ok)
    ok, out = await call(D, "room_archive", room="renamed-room")
    check("archive", ok and out["archived"] is True, str(out))
    ok, out = await call(D, "room_list")
    check("archived hidden by default", "renamed-room" not in [r["name"] for r in out["rooms"]])
    ok, out = await call(D, "room_list", include_archived=True)
    row = [r for r in out["rooms"] if r["name"] == "renamed-room"]
    check("include_archived shows flagged", len(row) == 1 and row[0]["archived"] is True, str(out))
    ok, out = await call(D, "room_post", room="renamed-room", body="archived still writable by design")
    check("archived room still postable (visibility only)", ok, str(out))
    ok, out = await call(D, "room_archive", room="renamed-room", archived=False)
    check("unarchive", ok and out["archived"] is False)
    ok, out = await call(D, "room_list")
    check("unarchived visible again", "renamed-room" in [r["name"] for r in out["rooms"]])
    ok, out = await call(D, "room_archive", room="pack-main")
    check("archive main rejected", not ok)
    ok, out = await call(D, "room_archive", room="ghost-room")
    check("archive ghost rejected", not ok)
    ok, out = await call(A, "room_archive", room="renamed-room")
    check("member cannot archive", not ok)

    print("== H3 头像组合 ==")
    ok, out = await call(D, "member_list")
    combos = {}
    for m in out["members"]:
        av = m.get("avatar") or {}
        check(f"avatar combo present: {m['name']}", bool(av.get("combo")), str(av))
        check(f"avatar v1 compat: {m['name']}", bool(av.get("glyph") and av.get("c1") and av.get("c2")))
        combos[m["name"]] = av.get("combo")
    check("combos unique (incl dispatcher backfill)", len(set(combos.values())) == len(combos), str(combos))
    for i in range(12):
        ok, out = await call(D, "member_register", name=f"m{i}")
        check(f"register m{i}", ok, str(out))
    ok, out = await call(D, "member_list")
    allc = [(m["avatar"] or {}).get("combo") for m in out["members"]]
    check("16 members combos all unique", len(set(allc)) == len(allc) == 16, str(allc))

    print("== 并发注册避撞 ==")
    async def reg(i):
        return await call(D, "member_register", name=f"cc{i}")
    res = await asyncio.gather(*[reg(i) for i in range(8)])
    check("8 parallel registers all ok", all(r[0] for r in res), str(res))
    ok, out = await call(D, "member_list")
    allc = [(m["avatar"] or {}).get("combo") for m in out["members"]]
    check("24 members combos all unique", len(set(allc)) == len(allc) == 24, str(len(allc)))

    print("== 回归 ==")
    ok, out = await call(D, "room_post", room="pack-main", body="regression ping")
    check("post", ok, str(out))
    ok, out = await call(A, "room_read_new", room="pack-main", since_id=0)
    check("read", ok and any(m["body"] == "regression ping" for m in out["messages"]), str(out)[:120])
    ok, out = await call(D, "task_create", room="pack-main", title="reg task", body="b", assignee_name="alice")
    check("task_create", ok, str(out))
    ok, out = await call(D, "skill_list")
    check("skill_list", ok, str(out)[:80])

    print(f"RESULT pass={PASS} fail={FAIL}")
    return FAIL

if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
