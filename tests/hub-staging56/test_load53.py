"""staging53 轻量负载探针（对抗复检清单）：50 张任务卡各走一遍
create→claim→question→answer 全周期，最后核对 DB 计数与
_question_state 口径（经 room_read_new 回读 payload 校验）。
须在独立全新 DB 上跑（计数按全库断言）：run_tests.sh 第四段负责
fresh_db + 起服；单跑时先手动起隔离中枢（18798、全新 DB）。"""
import asyncio, json, sqlite3, sys, time
sys.path.insert(0, ".")
from harness import call, DISP, DB

N = 50
res = []


def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)


async def main():
    alice = (await call(DISP, "member_register", name="loadalice"))["token"]
    payload = json.dumps({"options": ["甲", "乙"], "allow_input": True},
                         ensure_ascii=False)
    t0 = time.time()
    for i in range(N):
        tid = (await call(DISP, "task_create", room="pack-main",
                          title=f"load-{i}", body="b",
                          assignee_name="loadalice"))["task_id"]
        await call(alice, "task_claim", task_id=tid)
        q = await call(alice, "room_post", room="pack-main", kind="question",
                       body=f"任务#{tid} 问题{i}", payload=payload)
        await call(DISP, "room_post", room="pack-main",
                   kind="question_answer", body=f"答{i}",
                   reply_to_id=q["message_id"])
    dt = time.time() - t0
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    nq = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE kind='question'").fetchone()["n"]
    na = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE kind='question_answer'").fetchone()["n"]
    nt = conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
    orphans = conn.execute(
        "SELECT COUNT(*) AS n FROM messages a WHERE a.kind='question_answer'"
        " AND NOT EXISTS (SELECT 1 FROM messages q WHERE q.id=a.reply_to_id"
        " AND q.kind='question')").fetchone()["n"]
    unanswered = conn.execute(
        "SELECT COUNT(*) AS n FROM messages q WHERE q.kind='question'"
        " AND NOT EXISTS (SELECT 1 FROM messages a WHERE a.kind='question_answer'"
        " AND a.reply_to_id=q.id)").fetchone()["n"]
    conn.close()
    note(f"load: {N} tasks created", nt == N, f"tasks={nt}")
    note(f"load: {N} questions stored", nq == N, f"q={nq}")
    note(f"load: {N} answers stored", na == N, f"a={na}")
    note("load: no orphan answers", orphans == 0)
    note("load: no unanswered questions left", unanswered == 0)
    print(f"load: {N} cycles in {dt:.1f}s")
    print(f"== {sum(res)}/{len(res)} PASS")
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
