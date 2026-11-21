"""staging33 定向测试：member_avatar_set（更换头像写路径）。

覆盖：正常更换 / 幂等重设 / 占用拒绝 / 参数校验（越界、缺字段、坏 JSON、
非对象）/ 不存在成员 / 已移除成员 / 成员身份越权 / glyph 与渐变保留 /
member_list 回读一致。
"""
import asyncio, json, sys
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging33")
from harness import call, setup_members, DISP, TOKENS

FAIL = []

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)

COMBO_A = {"species": "wolf", "palette": "teal", "accessory": "scarf", "bg": "sand"}
KEY_A = "wolf|teal|scarf|sand"
COMBO_B = {"species": "cat", "palette": "rose", "accessory": "glasses", "bg": "sky"}

async def main():
    await setup_members()
    alice = TOKENS["alice"]

    ok, before = await call(DISP, "member_list")
    a0 = [m for m in before["members"] if m["name"] == "alice"][0]
    glyph0, c1_0 = a0["avatar"].get("glyph"), a0["avatar"].get("c1")

    ok, r = await call(DISP, "member_avatar_set", name="alice",
                       avatar_combo=json.dumps(COMBO_A))
    check("set ok", ok and r["avatar"]["combo"] == KEY_A, str(r))
    check("glyph preserved", r["avatar"].get("glyph") == glyph0)
    check("gradient preserved", r["avatar"].get("c1") == c1_0)

    ok, r = await call(DISP, "member_avatar_set", name="alice",
                       avatar_combo=json.dumps(COMBO_A))
    check("idempotent re-set", ok and r["avatar"]["combo"] == KEY_A, str(r))

    ok, r = await call(DISP, "member_avatar_set", name="bob",
                       avatar_combo=json.dumps(COMBO_A))
    check("taken combo rejected", not ok and "taken" in str(r), str(r))

    ok, r = await call(DISP, "member_avatar_set", name="bob",
                       avatar_combo=json.dumps(COMBO_B))
    check("bob own combo ok", ok and r["avatar"]["species"] == "cat", str(r))

    ok, r = await call(DISP, "member_avatar_set", name="alice",
                       avatar_combo=json.dumps({**COMBO_B, "species": "dragon"}))
    check("bad species rejected", not ok and "bad species" in str(r), str(r))

    ok, r = await call(DISP, "member_avatar_set", name="alice",
                       avatar_combo=json.dumps({"species": "fox"}))
    check("missing field rejected", not ok and "missing" in str(r), str(r))

    ok, r = await call(DISP, "member_avatar_set", name="alice",
                       avatar_combo="{not json")
    check("malformed json rejected", not ok and "not valid JSON" in str(r), str(r))

    ok, r = await call(DISP, "member_avatar_set", name="alice",
                       avatar_combo=json.dumps(["fox"]))
    check("non-object rejected", not ok and "must be a JSON object" in str(r), str(r))

    ok, r = await call(DISP, "member_avatar_set", name="nobody",
                       avatar_combo=json.dumps(COMBO_B))
    check("unknown member rejected", not ok and "no such member" in str(r), str(r))

    ok, r = await call(DISP, "member_register", name="carol")
    check("carol registered", ok, str(r))
    ok, r = await call(DISP, "member_remove", name="carol")
    check("carol removed", ok, str(r))
    ok, r = await call(DISP, "member_avatar_set", name="carol",
                       avatar_combo=json.dumps(COMBO_B))
    check("removed member rejected", not ok and "removed" in str(r), str(r))

    ok, r = await call(alice, "member_avatar_set", name="alice",
                       avatar_combo=json.dumps(COMBO_B))
    check("member self-call forbidden", not ok, str(r))

    ok, after = await call(DISP, "member_list")
    a1 = [m for m in after["members"] if m["name"] == "alice"][0]
    check("member_list reflects new combo", a1["avatar"]["combo"] == KEY_A)

    print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
