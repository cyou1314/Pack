"""staging17 专测：房间摘要卡 room_summary_get/set。"""
import asyncio, json, sys
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging17")
from harness import call, setup_members

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok {name}")
    else: FAIL += 1; print(f"  FAIL {name} {extra}")

async def main():
    toks = await setup_members()
    D, A = toks["dispatcher"], toks["alice"]

    ok, out = await call(A, "room_summary_get", room="pack-main")
    check("empty summary shape", ok and out["summary"] == {"decided": [], "in_progress": [], "pending": []}
          and out["updated_at"] is None, str(out))

    S = {"decided": ["UI 封板 §1-§15"], "in_progress": ["第一批 B1-1 外壳"], "pending": ["模板三问"]}
    ok, out = await call(D, "room_summary_set", room="pack-main", summary=json.dumps(S, ensure_ascii=False))
    check("set ok", ok and out["summary"] == S and out["updated_by"] == "dispatcher", str(out))
    ok, out = await call(A, "room_summary_get", room="pack-main")
    check("member reads back", ok and out["summary"] == S and out["updated_at"] is not None, str(out))

    ok, out = await call(D, "room_summary_set", room="pack-main",
                         summary=json.dumps({"decided": ["只有一组"]}))
    check("partial sections default empty", ok and out["summary"]["in_progress"] == []
          and out["summary"]["decided"] == ["只有一组"], str(out))

    bad = [
        ("not json", "{oops"),
        ("not object", json.dumps([1, 2])),
        ("unknown section", json.dumps({"bogus": []})),
        ("section not list", json.dumps({"decided": "x"})),
        ("item not string", json.dumps({"decided": [1]})),
        ("too many items", json.dumps({"decided": ["x"] * 21})),
        ("item too long", json.dumps({"decided": ["x" * 201]})),
        ("total too long", json.dumps({"decided": ["x" * 200] * 20, "pending": ["y" * 200] * 11})),
    ]
    for nm, payload in bad:
        ok, out = await call(D, "room_summary_set", room="pack-main", summary=payload)
        check(f"reject {nm}", not ok, str(out)[:80])
    ok, out = await call(A, "room_summary_get", room="pack-main")
    check("rejected writes left summary intact", ok and out["summary"]["decided"] == ["只有一组"], str(out))

    ok, out = await call(A, "room_summary_set", room="pack-main", summary=json.dumps(S))
    check("member cannot set", not ok)
    ok, out = await call(D, "room_summary_get", room="ghost-room")
    check("get ghost room rejected", not ok)
    ok, out = await call(D, "room_summary_set", room="ghost-room", summary=json.dumps(S))
    check("set ghost room rejected", not ok)

    ok, out = await call(D, "room_create", name="room-b")
    ok, out = await call(D, "room_summary_set", room="room-b", summary=json.dumps({"pending": ["B 房的事"]}))
    check("second room set", ok, str(out))
    ok, out = await call(D, "room_summary_get", room="pack-main")
    check("rooms independent", ok and out["summary"]["decided"] == ["只有一组"], str(out))
    ok, out = await call(D, "room_summary_get", room="room-b")
    check("room-b own summary", ok and out["summary"]["pending"] == ["B 房的事"], str(out))

    print(f"RESULT pass={PASS} fail={FAIL}")
    return FAIL

if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
