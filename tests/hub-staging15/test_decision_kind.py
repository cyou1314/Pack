"""staging15 批准链完整性专测：决议消息 kind='decision' 不可删改、不可重 filing。

覆盖：
A. 规则引擎自动决议 -> kind='decision'；dispatcher 本人 delete/edit 均被拒；
   正文完好；review_file 再 filing 被拒（已决议）。
B. review_file 决议 -> 同上四条。
C. 历史兼容：手工造 kind='chat' 旧式调度员决议，_approval_state 仍识别已决议。
D. 回归健全：普通 chat 消息仍可被作者删除（delete/edit 的 chat 通道没被误伤）。
"""
import asyncio, json, os, sqlite3, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, client, DISP, setup_members

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "pack.db")
PASS, FAIL = [], []

def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))

def sq(q, args=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = c.execute(q, args).fetchall(); c.close()
    return rows

def sqx(q, args=()):
    c = sqlite3.connect(DB); c.execute(q, args); c.commit(); c.close()

async def main():
    T = await setup_members()
    D, A = T["dispatcher"], T["alice"]

    # ---------- D. 普通 chat 删改通道健全 ----------
    ok, out = await call(A, "room_post", room="pack-main", body="临时消息", kind="chat")
    mid = out["message_id"] if ok and isinstance(out, dict) else None
    okd, _ = await call(A, "message_delete", message_id=mid)
    check("D1 普通 chat 消息作者可删除", bool(okd))

    # ---------- A. 规则引擎自动决议 ----------
    ok, _ = await call(D, "rule_upsert", action="[AUTO]", target="alice", effect="allow")
    check("A0 规则建立", ok)
    ok, t = await call(D, "task_create", room="pack-main",
                       title="[AUTO] 决议完整性甲", body="测试", assignee_name="alice")
    tid_a = t["task_id"] if ok else None
    ok, cl = await call(A, "task_claim", task_id=tid_a)
    check("A0b alice 先认领任务（F1 守卫要求持有人发批准请求）", ok)
    ok, out = await call(A, "room_post", room="pack-main",
                         body=f"申请批准 任务#{tid_a}：打开 https://example.com/a",
                         kind="approval_request")
    check("A1 规则命中自动决议", ok and isinstance(out, dict)
          and out.get("auto_decision") == "allow", str(out)[:120])
    rows = sq("SELECT * FROM messages WHERE task_id=? AND body LIKE '批准%'", (tid_a,))
    dec = rows[0] if rows else None
    check("A2 决议消息 kind='decision'", bool(dec) and dec["kind"] == "decision",
          f"kind={dec['kind'] if dec else None}")
    check("A3 决议正文带规则库后缀", bool(dec) and "规则库自动通过" in dec["body"])
    okd, errd = await call(D, "message_delete", message_id=dec["id"])
    check("A4 dispatcher 删决议被拒", not okd and "only chat" in str(errd), str(errd)[:100])
    oke, erre = await call(D, "message_edit", message_id=dec["id"],
                           new_body=f"批准 任务#{tid_a}（伪造改写）")
    check("A5 dispatcher 改决议被拒", not oke and "only chat" in str(erre), str(erre)[:100])
    rows = sq("SELECT body, deleted_at FROM messages WHERE id=?", (dec["id"],))
    check("A6 决议正文完好未软删", bool(rows) and "规则库自动通过" in rows[0]["body"]
          and rows[0]["deleted_at"] is None)
    okr, errr = await call(D, "review_file", task_id=tid_a,
                           target_url="https://example.com/a", owner_quote="原话",
                           member_desc="描述", verdict="deny")
    check("A7 已决议任务再 filing 被拒", not okr and "already decided" in str(errr),
          str(errr)[:120])

    # ---------- B. review_file 决议 ----------
    ok, t = await call(D, "task_create", room="pack-main",
                       title="[MANU] 决议完整性乙", body="测试", assignee_name="alice")
    tid_b = t["task_id"] if ok else None
    ok, cl = await call(A, "task_claim", task_id=tid_b)
    ok, out = await call(A, "room_post", room="pack-main",
                         body=f"申请批准 任务#{tid_b}：打开 https://example.com/b",
                         kind="approval_request")
    check("B1 无规则命中不自动决议", ok and isinstance(out, dict)
          and not out.get("auto_decision"), str(out)[:120])
    ok, rv = await call(D, "review_file", task_id=tid_b,
                        target_url="https://example.com/b", owner_quote="原话",
                        member_desc="描述", verdict="deny")
    check("B2 review_file 决议成功", ok and rv.get("verdict") == "deny", str(rv)[:120])
    mid_b = rv["message_id"] if ok else None
    rows = sq("SELECT * FROM messages WHERE id=?", (mid_b,))
    dec_b = rows[0] if rows else None
    check("B3 审查决议 kind='decision'", bool(dec_b) and dec_b["kind"] == "decision",
          f"kind={dec_b['kind'] if dec_b else None}")
    check("B4 审查决议正文格式", bool(dec_b)
          and dec_b["body"] == f"拒绝 任务#{tid_b}（独立审查）")
    okd, errd = await call(D, "message_delete", message_id=mid_b)
    check("B5 dispatcher 删审查决议被拒", not okd and "only chat" in str(errd),
          str(errd)[:100])
    oke, erre = await call(D, "message_edit", message_id=mid_b,
                           new_body=f"批准 任务#{tid_b}")
    check("B6 dispatcher 改审查决议被拒", not oke and "only chat" in str(erre),
          str(erre)[:100])
    okr, errr = await call(D, "review_file", task_id=tid_b,
                           target_url="https://example.com/b", owner_quote="原话",
                           member_desc="描述", verdict="allow")
    check("B7 审查后任务再 filing 被拒", not okr and "already decided" in str(errr),
          str(errr)[:120])

    # ---------- C. 历史 kind='chat' 决议兼容 ----------
    ok, t = await call(D, "task_create", room="pack-main",
                       title="[MANU] 决议完整性丙", body="测试", assignee_name="alice")
    tid_c = t["task_id"] if ok else None
    ok, cl = await call(A, "task_claim", task_id=tid_c)
    await call(A, "room_post", room="pack-main",
               body=f"申请批准 任务#{tid_c}：打开 https://example.com/c",
               kind="approval_request")
    disp = sq("SELECT id FROM members WHERE role='dispatcher'")[0]
    room = sq("SELECT id FROM rooms WHERE name='pack-main'")[0]
    sqx("INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
        "VALUES(?,?,'chat',?,?,?)",
        (room["id"], disp["id"], f"批准 任务#{tid_c}", tid_c, time.time()))
    okr, errr = await call(D, "review_file", task_id=tid_c,
                           target_url="https://example.com/c", owner_quote="原话",
                           member_desc="描述", verdict="allow")
    check("C1 历史 chat 决议仍被识别为已决议", not okr
          and "already decided" in str(errr), str(errr)[:120])

    print(f"\n==== decision-kind: {len(PASS)} passed, {len(FAIL)} failed ====")
    if FAIL:
        print("FAILED:", FAIL)
    return 1 if FAIL else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
