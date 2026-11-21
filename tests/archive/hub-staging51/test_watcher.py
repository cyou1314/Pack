import asyncio, json, os, subprocess, sys, time
sys.path.insert(0, ".")
from harness import call, DISP, HERE

res = []
FX = json.load(open(os.path.join(HERE, "test_fixtures.json")))
OWNER = FX["owner"]


def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)


async def wait_for(fn, timeout=75):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = await fn()
        if v:
            return v
        await asyncio.sleep(3)
    return None


async def main():
    state = os.path.join(HERE, ".room_watch_state.json")
    if os.path.exists(state):
        os.remove(state)
    env = dict(os.environ, PACK_WATCH_URL="http://127.0.0.1:18798/mcp")
    logf = open(os.path.join(HERE, "watcher-test.log"), "w")
    proc = subprocess.Popen([sys.executable, "room_watch.py"], cwd=HERE, env=env,
                            stdout=logf, stderr=subprocess.STDOUT)
    try:
        await asyncio.sleep(14)  # watcher 首轮：各房从当前最新消息起步

        async def task_ids():
            tl = await call(DISP, "task_list")
            return {t["id"] for t in tl["tasks"]}

        base = await task_ids()

        # 防线一（跨房）：owner 在 room-b @ 绑定 pack-main 的 external
        await call(OWNER, "room_post", room="room-b", kind="chat",
                   body="@codex-a 帮忙看一下这边的活")

        async def reject_b():
            rd = await call(DISP, "room_read_new", room="room-b", since_id=0, limit=50)
            return [m for m in rd["messages"] if "没转成任务" in m["body"]]

        rej = await wait_for(reject_b)
        note("watcher: cross-room @ to external -> rejection posted in that room",
             bool(rej))
        if rej:
            note("watcher: rejection explains the bound room",
                 "绑定的房间" in rej[0]["body"], rej[0]["body"][:60])
        note("watcher: no task created for cross-room @", await task_ids() == base)
        await asyncio.sleep(22)
        rej2 = await reject_b()
        note("watcher: rejection posted exactly once (no retry loop)",
             len(rej2) == 1, f"n={len(rej2)}")

        # 防线一（引擎标记）：owner 在绑定房 @ external 但正文带 [ENG]
        rd = await call(DISP, "room_read_new", room="pack-main", since_id=0,
                        limit=200, tail=True)
        last_main = rd["messages"][-1]["id"] if rd["messages"] else 0
        await call(OWNER, "room_post", room="pack-main", kind="chat",
                   body="@codex-a [ENG] 帮忙截个图")

        async def reject_main():
            r2 = await call(DISP, "room_read_new", room="pack-main",
                            since_id=last_main, limit=50)
            return [m for m in r2["messages"] if "没转成任务" in m["body"]]

        rej = await wait_for(reject_main)
        note("watcher: engine-marked @ to external -> rejection posted", bool(rej))
        if rej:
            note("watcher: rejection explains engine-marked ban",
                 "引擎标记" in rej[0]["body"], rej[0]["body"][:60])
        note("watcher: no task created for pinned @", await task_ids() == base)

        # 正常 @ 照常转卡
        await call(OWNER, "room_post", room="pack-main", kind="chat",
                   body="@codex-a 写一份简短摘要")

        async def new_task():
            ids = await task_ids()
            return (ids - base) or None

        new = await wait_for(new_task)
        note("watcher: normal @ still converts to a task", bool(new))
        if new:
            tid = list(new)[0]
            tl = await call(DISP, "task_list")
            t = [x for x in tl["tasks"] if x["id"] == tid][0]
            note("watcher: converted task assigned to codex-a",
                 t["assignee"] == "codex-a")
            base = base | new

        # 防线二：调度员 @ external 只问答、不开卡（watcher 只认 owner）
        await call(DISP, "room_post", room="pack-main", kind="chat",
                   body="@codex-a 在吗，问个问题")
        await asyncio.sleep(28)
        note("watcher: dispatcher @ does not create a task",
             await task_ids() == base)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        logf.close()
    print(f"== {sum(res)}/{len(res)} PASS", flush=True)
    if not all(res):
        sys.exit(1)


asyncio.run(main())
