"""staging18 专测：任务模板 template_upsert/list/delete/fire。"""
import asyncio, sys
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging18")
from harness import call, setup_members

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok {name}")
    else: FAIL += 1; print(f"  FAIL {name} {extra}")

async def main():
    toks = await setup_members()
    D, A = toks["dispatcher"], toks["alice"]

    ok, out = await call(D, "template_upsert", name="出图",
                         title_tpl="[ENG-image] {arg}",
                         body_tpl="画面：{arg}\n尺寸 21:9，落盘 ~/pack-engine/output/",
                         assignee="alice")
    check("upsert", ok and out["name"] == "出图", str(out))
    ok, out = await call(D, "template_list")
    check("list", ok and len(out["templates"]) == 1 and out["templates"][0]["assignee"] == "alice", str(out))

    ok, out = await call(D, "template_fire", name="出图", arg="雪山夜景")
    check("fire", ok and out["status"] == "open" and out["assignee"] == "alice" and out["template"] == "出图", str(out))
    tid = out["task_id"]
    ok, out = await call(D, "task_list")
    t = [x for x in out["tasks"] if x["id"] == tid][0]
    check("card assembled", t["title"] == "[ENG-image] 雪山夜景" and "雪山夜景" in t["body"], str(t))

    ok, out = await call(A, "task_claim", task_id=tid)
    check("fired card claimable (same state machine)", ok, str(out))

    ok, out = await call(D, "template_upsert", name="出图",
                         title_tpl="[ENG-image] v2 {arg}", body_tpl="新正文 {arg}", assignee="")
    check("upsert updates", ok, str(out))
    ok, out = await call(D, "template_fire", name="出图", arg="雪桥")
    check("fire uses new version, no default assignee",
          ok and out["assignee"] is None, str(out))
    tid2 = out["task_id"]
    ok, out = await call(D, "template_fire", name="出图", arg="雪桥", assignee_override="bob")
    check("assignee override", ok and out["assignee"] == "bob", str(out))

    ok, out = await call(D, "template_upsert", name="纯标题", title_tpl="固定标题", body_tpl="固定正文")
    ok, out = await call(D, "template_fire", name="纯标题", arg="多余参数")
    check("arg without placeholder rejected", not ok, str(out))
    ok, out = await call(D, "template_fire", name="纯标题")
    check("fire without arg ok", ok, str(out))

    bad = [
        ({"name": "", "title_tpl": "t", "body_tpl": "b"}, "empty name"),
        ({"name": "x" * 41, "title_tpl": "t", "body_tpl": "b"}, "name too long"),
        ({"name": "n2", "title_tpl": "", "body_tpl": "b"}, "empty title"),
        ({"name": "n3", "title_tpl": "t" * 201, "body_tpl": "b"}, "title too long"),
        ({"name": "n4", "title_tpl": "t", "body_tpl": "b" * 4001}, "body too long"),
        ({"name": "n5", "title_tpl": "t", "body_tpl": "b", "assignee": "ghost"}, "ghost assignee"),
    ]
    for kw, nm in bad:
        ok, out = await call(D, "template_upsert", **kw)
        check(f"reject {nm}", not ok, str(out)[:80])
    ok, out = await call(D, "template_fire", name="出图", arg="x" * 1001)
    check("reject arg too long", not ok)
    ok, out = await call(D, "template_fire", name="不存在的模板", arg="x")
    check("fire ghost template rejected", not ok)
    ok, out = await call(D, "template_fire", name="出图", arg="x", room="ghost-room")
    check("fire ghost room rejected", not ok)

    ok, out = await call(A, "template_upsert", name="hack", title_tpl="t", body_tpl="b")
    check("member cannot upsert", not ok)
    ok, out = await call(A, "template_list")
    check("member cannot list", not ok)
    ok, out = await call(A, "template_fire", name="出图", arg="x")
    check("member cannot fire", not ok)
    ok, out = await call(A, "template_delete", name="出图")
    check("member cannot delete", not ok)

    ok, out = await call(D, "template_delete", name="纯标题")
    check("delete", ok, str(out))
    ok, out = await call(D, "template_fire", name="纯标题")
    check("fire deleted rejected", not ok)
    ok, out = await call(D, "task_list")
    check("fired cards survive template delete",
          any(x["id"] == tid for x in out["tasks"]), "")
    ok, out = await call(D, "template_delete", name="纯标题")
    check("delete twice rejected", not ok)

    print(f"RESULT pass={PASS} fail={FAIL}")
    return FAIL

if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
