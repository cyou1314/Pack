#!/usr/bin/env python3
"""hub-staging31 定向测试：idx_messages_room 索引增量（第 6 轮深查 P1）。

只对 staging31 副本操作，不连生产 hub、不写生产库。
跑法：
  cd hub-staging31
  /home/hatch/workspace/pack/hub/.venv/bin/python hub.py init   # 在副本上应用 SCHEMA（含新索引）
  /home/hatch/workspace/pack/hub/.venv/bin/python test_idx_messages_room.py
断言：
  T1 SCHEMA 含 idx_messages_room，且 hub.py 内 "redacted" 字面量为 0；
  T2 索引已建在副本库上；
  T3 房间消息热查询走 SEARCH USING INDEX（原为全表 SCAN）；
  T4 room_list 的 MAX(m.id) 子查询走 SEARCH USING INDEX；
  T5 索引前后两条热查询结果逐行一致；
  T6 task_audit 的 audit 查询计划与结果不受影响（仍走 id 主键排序）。
"""
import sqlite3
import subprocess
import sys

D = "/home/hatch/workspace/pack/hub-staging31"
ROOM_Q = """SELECT m.id, m.kind, m.body FROM messages m
            WHERE m.room_id=? AND m.id>? ORDER BY m.id ASC LIMIT ?"""
MAX_Q = """SELECT MAX(m.id) FROM messages m WHERE m.room_id=?"""
AUDIT_Q = """SELECT ts, actor, action, detail FROM audit
             WHERE detail=? OR detail LIKE ? OR detail LIKE ?
             ORDER BY id ASC LIMIT 201"""

passed = failed = 0


def check(name, ok, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"PASS {name} {extra}")
    else:
        failed += 1
        print(f"FAIL {name} {extra}")


src = open(f"{D}/hub.py", encoding="utf-8").read()
check("T1-schema-has-index", "CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, id);" in src)
check("T1-no-redacted-literal", src.count("redacted") == 0, f"count={src.count('redacted')}")

idx = sqlite3.connect(f"{D}/pack.db")
rows = idx.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_messages_room'").fetchall()
check("T2-index-exists-on-copy", len(rows) == 1)

plan = "\n".join(r[3] for r in idx.execute("EXPLAIN QUERY PLAN " + ROOM_Q, (1, 0, 50)))
check("T3-room-read-uses-index", "USING INDEX idx_messages_room" in plan and "SCAN" not in plan, plan.replace("\n", " | "))
plan2 = "\n".join(r[3] for r in idx.execute("EXPLAIN QUERY PLAN " + MAX_Q, (1,)))
check("T4-max-uses-index", "idx_messages_room" in plan2 and "SCAN" not in plan2, plan2.replace("\n", " | "))

pris = sqlite3.connect(f"{D}/pack-pristine.db")
for room_id in (1, 2, 3):
    a = idx.execute(ROOM_Q, (room_id, 0, 50)).fetchall()
    b = pris.execute(ROOM_Q, (room_id, 0, 50)).fetchall()
    check(f"T5-room-read-identical-room{room_id}", a == b, f"rows={len(a)}")
    a2 = idx.execute(MAX_Q, (room_id,)).fetchall()
    b2 = pris.execute(MAX_Q, (room_id,)).fetchall()
    check(f"T5-max-identical-room{room_id}", a2 == b2)

for tid in (1, 23):
    pre = f"task {tid}"
    a = idx.execute(AUDIT_Q, (pre, pre + ":%", pre + " %")).fetchall()
    b = pris.execute(AUDIT_Q, (pre, pre + ":%", pre + " %")).fetchall()
    check(f"T6-audit-identical-task{tid}", a == b, f"events={len(a)}")
plan3 = "\n".join(r[3] for r in idx.execute("EXPLAIN QUERY PLAN " + AUDIT_Q, ("task 1", "task 1:%", "task 1 %")))
check("T6-audit-plan-unchanged", "SCAN audit" in plan3, plan3.replace("\n", " | "))

print(f"RESULT pass={passed} fail={failed}")
sys.exit(1 if failed else 0)
