#!/usr/bin/env python3
"""Pack hub Skill 模组 S1 专测（只打 staging13：8813 + 全新 DB）。
覆盖：工具数 43 含六个 skill 工具 / publish v1→v2 版本递增且全量留版 /
成员读未指派被拒 / assign 后可读、目录只见自己的 / 成员间隔离 /
调度员全读 / rollback 生成新版本且正文等于目标版、回滚不存在版本被拒 /
超限 publish（正文 20001 字、简介 201 字）整笔拒绝不落版 /
assign 不存在的成员与 skill 被拒 / unassign 后读不到、再指派恢复 /
非调度员 publish 被拒 / 原 37 工具关键回归抽测
（发帖读帖、建房列表、任务全链带信封、转交+领令牌、记忆写读）。"""
import asyncio
import json
import sqlite3

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "http://127.0.0.1:8813/mcp"
BASE = "/home/hatch/workspace/pack/hub-staging15"

passed = 0
failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


def client(token):
    return Client(StreamableHttpTransport(URL, headers={"Authorization": f"Bearer {token}"}))


def val(res):
    if getattr(res, "structured_content", None):
        return res.structured_content
    return json.loads(res.content[0].text)


async def call(token, tool, **kw):
    async with client(token) as c:
        return val(await c.call_tool(tool, kw))


async def call_err(token, tool, **kw):
    try:
        r = await call(token, tool, **kw)
        return f"NO-ERROR: {r}"
    except Exception as e:
        return str(e)


async def tools_of(token):
    async with client(token) as c:
        return [t.name for t in await c.list_tools()]


def sql(q, args=()):
    conn = sqlite3.connect(f"{BASE}/pack.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return rows


async def main():
    disp = open(f"{BASE}/.dispatcher_token").read().strip()

    # ---------- 工具面 ----------
    tools = await tools_of(disp)
    check("工具数 71", len(tools) == 71, f"got {len(tools)}")
    check("含六个 skill 工具", all(t in tools for t in (
        "skill_publish", "skill_list", "skill_read",
        "skill_assign", "skill_unassign", "skill_rollback")))

    # ---------- 成员 ----------
    absent = (await call(disp, "member_register", name="absent"))["token"]
    bert = (await call(disp, "member_register", name="bert"))["token"]

    # ---------- publish 与版本 ----------
    r = await call(disp, "skill_publish", name="web-research",
                   body="规程 v1：先查来源再引用。", description="网页调研规程")
    check("publish 新建 version=1", r["version"] == 1 and r["name"] == "web-research", str(r))
    r = await call(disp, "skill_publish", name="web-research",
                   body="规程 v2：先查来源、交叉核对、再引用。")
    check("publish 更新 version=2", r["version"] == 2, str(r))
    rows = sql("SELECT version FROM skill_versions ORDER BY version")
    check("skill_versions 全量留版 2 行", [x["version"] for x in rows] == [1, 2],
          str([dict(x) for x in rows]))
    lst = (await call(disp, "skill_list"))["skills"]
    check("调度员目录含该 skill 且简介保留",
          len(lst) == 1 and lst[0]["version"] == 2 and lst[0]["description"] == "网页调研规程",
          str(lst))

    e = await call_err(absent, "skill_publish", name="x", body="y")
    check("非调度员 publish 被拒", "forbidden" in e, e)

    # ---------- 指派前不可见不可读 ----------
    check("成员目录未指派为空", (await call(absent, "skill_list"))["skills"] == [])
    e = await call_err(absent, "skill_read", name="web-research")
    check("读未指派被拒", "not assigned" in e, e)

    # ---------- assign 校验 ----------
    e = await call_err(disp, "skill_assign", name="web-research", member_name="ghost")
    check("assign 不存在成员被拒", "no such member" in e, e)
    e = await call_err(disp, "skill_assign", name="no-skill", member_name="absent")
    check("assign 不存在 skill 被拒", "no such skill" in e, e)

    # ---------- assign 后可读 + 隔离 ----------
    r = await call(disp, "skill_assign", name="web-research", member_name="absent")
    check("assign 成功", r["enabled"] is True, str(r))
    lst = (await call(absent, "skill_list"))["skills"]
    check("absent 目录见已指派", [x["name"] for x in lst] == ["web-research"], str(lst))
    rd = await call(absent, "skill_read", name="web-research")
    check("absent 读到 v2 正文",
          rd["version"] == 2 and rd["body"] == "规程 v2：先查来源、交叉核对、再引用。", str(rd))
    check("bert 目录为空（隔离）", (await call(bert, "skill_list"))["skills"] == [])
    e = await call_err(bert, "skill_read", name="web-research")
    check("bert 读他人 skill 被拒（隔离）", "not assigned" in e, e)
    rd = await call(disp, "skill_read", name="web-research")
    check("调度员可读全部", rd["body"].startswith("规程 v2"), str(rd))

    # ---------- rollback ----------
    e = await call_err(disp, "skill_rollback", name="web-research", version=99)
    check("回滚不存在版本被拒", "no such version" in e, e)
    r = await call(disp, "skill_rollback", name="web-research", version=1)
    check("rollback 生成新版本 3", r["version"] == 3 and r["restored_from"] == 1, str(r))
    rd = await call(absent, "skill_read", name="web-research")
    check("回滚后正文等于 v1", rd["version"] == 3 and rd["body"] == "规程 v1：先查来源再引用。",
          str(rd))
    rows = sql("SELECT version FROM skill_versions ORDER BY version")
    check("回滚不删历史（3 行）", [x["version"] for x in rows] == [1, 2, 3],
          str([dict(x) for x in rows]))

    # ---------- unassign / 再指派 ----------
    await call(disp, "skill_unassign", name="web-research", member_name="absent")
    e = await call_err(absent, "skill_read", name="web-research")
    check("unassign 后读不到", "not assigned" in e, e)
    await call(disp, "skill_assign", name="web-research", member_name="absent")
    rd = await call(absent, "skill_read", name="web-research")
    check("再指派后恢复可读", rd["version"] == 3, str(rd))

    # ---------- 超限整笔拒绝 ----------
    before = sql("SELECT COUNT(*) AS c FROM skill_versions")[0]["c"]
    e = await call_err(disp, "skill_publish", name="web-research", body="x" * 20001)
    check("正文 20001 字被拒", "skill_limit_exceeded" in e, e[:200])
    after = sql("SELECT COUNT(*) AS c FROM skill_versions")[0]["c"]
    check("超限不落版", before == after, f"{before} -> {after}")
    e = await call_err(disp, "skill_publish", name="big-new", body="x" * 20001)
    check("超限新建也被拒", "skill_limit_exceeded" in e, e[:200])
    names = [x["name"] for x in (await call(disp, "skill_list"))["skills"]]
    check("超限新建不落库", "big-new" not in names, str(names))
    e = await call_err(disp, "skill_publish", name="d-long", body="ok",
                       description="d" * 201)
    check("简介 201 字被拒", "skill_desc_limit_exceeded" in e, e[:200])
    r = await call(disp, "skill_publish", name="exact-20000", body="y" * 20000,
                   description="刚好两万字")
    check("正文整 20000 字可发布", r["version"] == 1, str(r))

    # ---------- 审计不记正文 ----------
    rows = sql("SELECT action, detail FROM audit WHERE action LIKE 'skill_%'")
    actions = {x["action"] for x in rows}
    check("审计含四类 skill 动作",
          {"skill_publish", "skill_assign", "skill_unassign", "skill_rollback"} <= actions,
          str(actions))
    check("审计不含正文", all("规程" not in (x["detail"] or "") for x in rows))

    # ---------- 原 37 工具关键回归抽测 ----------
    m = await call(absent, "room_post", room="pack-main", body="regression hello")
    msgs = (await call(disp, "room_read_new", room="pack-main", since_id=0))["messages"]
    check("回归：发帖读帖", any(x["id"] == m["message_id"] for x in msgs))
    rc = await call(disp, "room_create", name="reg-room")
    rl = (await call(absent, "room_list"))["rooms"]
    check("回归：建房与房间列表",
          rc["name"] == "reg-room" and any(x["name"] == "reg-room" for x in rl))

    t = await call(disp, "task_create", room="pack-main", title="reg task", body="b",
                   assignee_name="absent")
    cl = await call(absent, "task_claim", task_id=t["task_id"])
    done = await call(absent, "task_complete", task_id=t["task_id"],
                      claim_token=cl["claim_token"], result="",
                      envelope={"verdict": "done", "summary": "reg ok"})
    check("回归：任务全链带信封", done["status"] == "review", str(done))

    t2 = await call(disp, "task_create", room="pack-main", title="reg transfer", body="b",
                    assignee_name="absent")
    cl2 = await call(absent, "task_claim", task_id=t2["task_id"])
    tr = await call(absent, "task_transfer", task_id=t2["task_id"],
                    claim_token=cl2["claim_token"], to_member="bert", reason="reg")
    ac = await call(bert, "task_accept_transfer", task_id=t2["task_id"])
    check("回归：转交+领令牌", bool(tr.get("transfer_id")) and bool(ac.get("claim_token")),
          f"{tr} / {ac}")

    await call(absent, "memory_write", scope="private-core", key="", text="reg core")
    mr = await call(absent, "memory_read", scope="private-core")
    check("回归：记忆写读", mr["text"] == "reg core", str(mr))

    print(f"\n{passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
