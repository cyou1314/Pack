"""staging51 进程内直测：附件尺寸/总量配额的 hub 侧精确口径。

为什么需要这一套：MCP SDK 的 Streamable HTTP 传输有 4MiB 请求体默认
上限（mcp/server/transport_security.py DEFAULT_MAX_REQUEST_BODY_SIZE），
生产同样受限——>~3MB 的文件经 HTTP 根本到不了工具层，hub 侧的 5MB
单文件上限与 50MB 总量上限无法经 HTTP 验证。本套件把 hub.py 复制到
独立目录 direct_env/ 导入，打桩模块级 authenticate()（工具函数都走它）
后直调 FastMCP 注册表里的工具函数本体，验的是 hub 逻辑本身。
传输上限这一既有限制已记入 REVIEW51。
"""
import asyncio
import base64
import importlib.util
import inspect
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, "direct_env")
res = []


def note(name, cond, extra=""):
    res.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)


def png(n):
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * max(0, n - 8)


def main():
    shutil.rmtree(ENV, ignore_errors=True)
    os.makedirs(ENV)
    shutil.copy(os.path.join(HERE, "hub.py"), os.path.join(ENV, "hub.py"))
    spec = importlib.util.spec_from_file_location("hub_direct", os.path.join(ENV, "hub.py"))
    hub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hub)
    hub.init()
    mcp = hub.build_server()

    def tool_fn(name):
        t = mcp.get_tool(name)
        if inspect.isawaitable(t):
            t = asyncio.new_event_loop().run_until_complete(t)
        return t.fn

    dbp = os.path.join(ENV, "pack.db")

    def row(name):
        c = sqlite3.connect(dbp)
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT * FROM members WHERE name=?", (name,)).fetchone()
        c.close()
        return dict(r) if r else None

    def sql(q, args=()):
        c = sqlite3.connect(dbp)
        c.execute(q, args)
        c.commit()
        c.close()

    cell = {"me": row("dispatcher")}
    hub.authenticate = lambda: cell["me"]

    put = tool_fn("attachment_put")
    reg = tool_fn("member_register")
    reg(name="din", provider="claude-code")
    din = row("din")

    now = int(time.time())
    c = sqlite3.connect(dbp)
    room_id = c.execute("SELECT id FROM rooms WHERE name='pack-main'").fetchone()[0]
    c.execute(
        "INSERT INTO members(name, token_hash, role, kind, provider, display_name,"
        " bound_room_id, joined_msg_id, avatar, created_at)"
        " VALUES('dext', ?, 'member', 'external', 'codex', 'dext', ?, 0, '{}', ?)",
        (hub.hash_token("direct-ext-token"), room_id, now))
    c.commit()
    c.close()
    dext = row("dext")
    FIVE = 5 * 1024 * 1024

    def try_put(me, n, fname):
        cell["me"] = me
        try:
            r = put(room="pack-main", filename=fname, mime="image/png",
                    data_base64=base64.b64encode(png(n)).decode())
            return True, r
        except Exception as e:
            return False, str(e)

    # 1) external 单文件 >5MB：hub 侧精确拒绝（错误文案来自 hub，不是传输层）
    ok, err = try_put(dext, 5_500_000, "big.png")
    note("direct: external >5MB rejected by hub check", not ok and "5MB" in err, err[:80])

    # 2) 恰 5MiB 放行
    ok, r = try_put(dext, FIVE, "exact5.png")
    note("direct: external exactly 5MiB ok", ok and r.get("size") == FIVE,
         str(r.get("size") if ok else r))

    # 3) 总量 50MB：再传 9 个 5MiB（合计 10 个 = 50MiB），每次传完把行龄
    #    推到 25 小时前，避开每日次数窗口（验的是总量、不是次数）。
    allok = True
    for i in range(9):
        ok, r = try_put(dext, FIVE, f"t{i}.png")
        allok = allok and ok
        if not ok:
            print("  !! total batch fail:", str(r)[:80], flush=True)
        sql("UPDATE attachments SET created_at=created_at-90000 WHERE uploader_id=?",
            (dext["id"],))
    note("direct: external 10 x 5MiB ok (50MiB total)", allok)
    ok, err = try_put(dext, 64, "tiny.png")
    note("direct: beyond 50MiB member total rejected", not ok and "total" in err, err[:80])
    c = sqlite3.connect(dbp)
    n_audit = c.execute("SELECT COUNT(*) FROM audit WHERE action='external_attachment_quota'"
                        " AND actor='dext'").fetchone()[0]
    total = c.execute("SELECT COALESCE(SUM(size),0) FROM attachments WHERE uploader_id=?",
                      (dext["id"],)).fetchone()[0]
    c.close()
    note("direct: total-quota rejection audited", n_audit >= 1)
    note("direct: member total is exactly 50MiB", total == 10 * FIVE, str(total))

    # 4) 内部成员走全员口径：6MB 放行、11MB 被全员 10MB 上限拒绝
    ok, r = try_put(din, 6_000_000, "six.png")
    note("direct: internal 6MB ok (global path)", ok and r.get("size") == 6_000_000)
    ok, err = try_put(din, 11_000_000, "eleven.png")
    note("direct: internal 11MB rejected by global 10MB cap",
         not ok and "too large" in err, err[:80])

    print(f"== {sum(res)}/{len(res)} PASS")
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
