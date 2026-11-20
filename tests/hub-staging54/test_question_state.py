"""staging53 进程内直测：_validate_question_payload 全分支与
_question_state 口径本体（不经 HTTP，直接导入 hub.py 在隔离目录建库）。
_question_state 没有工具面出口（本片只落函数），只能这样验本体；
顺带验 payload 列迁移在 init 后存在。"""
import importlib.util, json, os, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, "direct_env53")
res = []


def note(name, cond, extra=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)


def expect_bad(fn, label):
    try:
        fn()
    except ValueError:
        note(f"payload rejected: {label}", True)
        return
    note(f"payload rejected: {label}", False, "no ValueError")


def main():
    shutil.rmtree(ENV, ignore_errors=True)
    os.makedirs(ENV)
    shutil.copy(os.path.join(HERE, "hub.py"), os.path.join(ENV, "hub.py"))
    spec = importlib.util.spec_from_file_location("hub53", os.path.join(ENV, "hub.py"))
    hub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub)
    hub.init()

    conn = hub.db()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
    note("migration: messages.payload exists after init", "payload" in cols)

    # ---------- _validate_question_payload ----------
    v = hub._validate_question_payload
    out = json.loads(v('{"options":["甲","乙"],"allow_input":true}'))
    note("payload ok: options+input", out == {"options": ["甲", "乙"], "allow_input": True})
    out = json.loads(v('{"allow_input":true}'))
    note("payload ok: pure input normalized", out == {"options": [], "allow_input": True})
    out = json.loads(v('{"options":["甲","乙"]}'))
    note("payload ok: options only, allow_input defaults false",
         out == {"options": ["甲", "乙"], "allow_input": False})
    out = json.loads(v('{"options":["甲","乙"],"allow_input":false,"extra":1}'))
    note("payload ok: extra keys dropped", set(out.keys()) == {"options", "allow_input"})
    out = json.loads(v('{"options":["%s","乙"]}' % ("长" * 60)))
    note("payload ok: option at exactly 60 chars", len(out["options"][0]) == 60)
    for label, raw in [
        ("empty string", ""), ("none", None), ("garbage", "nope"),
        ("json array", "[1]"), ("json string", '"x"'), ("empty object", "{}"),
        ("one option", '{"options":["一"]}'),
        ("seven options", json.dumps({"options": [str(i) for i in range(7)]})),
        ("option 61 chars", json.dumps({"options": ["长" * 61, "乙"]})),
        ("empty option", '{"options":["","乙"]}'),
        ("blank option", '{"options":["  ","乙"]}'),
        ("non-string option", '{"options":[1,"乙"]}'),
        ("options not a list", '{"options":"甲乙","allow_input":true}'),
        ("allow_input string", '{"options":["甲","乙"],"allow_input":"yes"}'),
        ("allow_input int", '{"options":["甲","乙"],"allow_input":1}'),
        ("options empty + no input", '{"options":[],"allow_input":false}'),
        ("options null + no input", '{"options":null,"allow_input":false}'),
    ]:
        expect_bad(lambda raw=raw: v(raw), label)
    conn.close()

    # ---------- _question_state ----------
    conn = hub.db()
    now = time.time()
    alice_id = conn.execute(
        "INSERT INTO members(name,role,token_hash,created_at)"
        " VALUES('alice','member','y',?)", (now,)).lastrowid
    room_id = conn.execute(
        "SELECT id FROM rooms WHERE name='pack-main'").fetchone()["id"]
    conn.commit()

    def msg(kind, body, task_id=None, reply_to_id=None, payload=None):
        cur = conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at,reply_to_id,payload)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (room_id, alice_id, kind, body, task_id, now, reply_to_id, payload))
        return cur.lastrowid

    q7 = msg("question", "任务#7 问题", task_id=7, payload='{"options":["甲","乙"],"allow_input":false}')
    a7 = msg("question_answer", "答", task_id=7, reply_to_id=q7)
    q8 = msg("question", "任务#8 旧式问题（无 task_id 补写）", task_id=None)
    msg("chat", "任务#9 只是聊天里提到", task_id=None)
    q10a = msg("question", "任务#10 第一问", task_id=10)
    msg("question_answer", "答一", task_id=10, reply_to_id=q10a)
    q10b = msg("question", "任务#10 第二问", task_id=10)
    q12 = msg("question", "任务#12 问题", task_id=12)
    msg("question_answer", "串答", task_id=99, reply_to_id=q7)  # 指向别的 question
    conn.commit()

    q, a = hub._question_state(conn, 7)
    note("state: answered question found with its answer",
         q is not None and q["id"] == q7 and a is not None and a["id"] == a7)
    q, a = hub._question_state(conn, 8)
    note("state: legacy question found via body regex, unanswered",
         q is not None and q["id"] == q8 and a is None)
    q, a = hub._question_state(conn, 9)
    note("state: chat mention is not a question", q is None and a is None)
    q, a = hub._question_state(conn, 10)
    note("state: latest question wins, its own answer state",
         q is not None and q["id"] == q10b and a is None)
    q, a = hub._question_state(conn, 11)
    note("state: no question -> (None, None)", q is None and a is None)
    q, a = hub._question_state(conn, 12)
    note("state: answer to another question does not count",
         q is not None and q["id"] == q12 and a is None)
    conn.close()

    print(f"== {sum(res)}/{len(res)} PASS")
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
