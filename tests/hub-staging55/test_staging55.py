import asyncio, json, sys
sys.path.insert(0, ".")
from harness import call, DISP

res = []


def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)


async def main():
    async with __import__("harness").client(DISP) as c:
        tools = await c.list_tools()
    note("tools == 74 (no new tools)", len(tools) == 74, f"got {len(tools)}")

    ALICE = (await call(DISP, "member_register", name="alice"))["token"]
    tid = (await call(DISP, "task_create", room="pack-main", title="t-one",
                      body="b", assignee_name="alice"))["task_id"]
    await call(ALICE, "task_claim", task_id=tid)

    chat = await call(ALICE, "room_post", room="pack-main", kind="chat",
                      body="plain chat")
    q = await call(ALICE, "room_post", room="pack-main", kind="question",
                   body=f"任务#{tid} 用哪个方案？",
                   payload=json.dumps({"options": ["甲", "乙"], "allow_input": True},
                                      ensure_ascii=False))
    a = await call(DISP, "room_post", room="pack-main", kind="question_answer",
                   body="甲", reply_to_id=q["message_id"])

    for tail in (False, True):
        out = await call(DISP, "room_read_new", room="pack-main", since_id=0,
                         limit=50, tail=tail)
        msgs = {m["id"]: m for m in out["messages"]}
        tag = "tail" if tail else "asc"
        note(f"{tag}: all rows carry reply_to_id key",
             all("reply_to_id" in m for m in msgs.values()))
        note(f"{tag}: answer reply_to_id == question id",
             msgs[a["message_id"]]["reply_to_id"] == q["message_id"],
             f"got {msgs[a['message_id']].get('reply_to_id')}")
        note(f"{tag}: question reply_to_id is None",
             msgs[q["message_id"]]["reply_to_id"] is None)
        note(f"{tag}: chat reply_to_id is None",
             msgs[chat["message_id"]]["reply_to_id"] is None)
        note(f"{tag}: question payload intact",
             json.loads(msgs[q["message_id"]]["payload"])["options"] == ["甲", "乙"])

    print(f"== {sum(res)}/{len(res)} PASS")
    sys.exit(0 if all(res) else 1)


asyncio.run(main())
