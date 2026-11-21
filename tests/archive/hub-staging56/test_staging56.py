"""staging56 / 搬运四：成员级访问模式预设 access_mode 验收套件。

覆盖施工单「验收」节：工具数 75、member_list 归一回读、readonly 名单
内外逐一验证（名单内可用 / 五个写工具被拒且各有 access_mode_denied
审计 / heartbeat 例外可用）、workspace 禁名单、full 零变化、external +
readonly 双闸叠加（白名单内被模式闸拒、白名单外先被白名单闸拒且审计
不双写）、设置面五种拒绝（成员自调 / dispatcher 本人 / owner / 已移除
/ 不存在 / 非法 mode）、set 后立即生效、脏值归一、并发交错探针。
"""
import asyncio, base64, json, sqlite3, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, ".")
from harness import call, try_call, client, pair_post, DISP, DB

res = []


def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)


def dbq(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def dbx(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def audit_n(action, actor=None):
    if actor:
        return dbq("SELECT COUNT(*) AS n FROM audit WHERE action=? AND actor=?",
                   (action, actor))[0]["n"]
    return dbq("SELECT COUNT(*) AS n FROM audit WHERE action=?", (action,))[0]["n"]


def audit_details(action, actor):
    return [r["detail"] for r in dbq(
        "SELECT detail FROM audit WHERE action=? AND actor=? ORDER BY id",
        (action, actor))]


def png64(n=64):
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * (n - 8)).decode()


async def mode_of(tok, name):
    ml = await call(tok, "member_list")
    row = [m for m in ml["members"] if m["name"] == name]
    return row[0].get("access_mode") if row else None


async def make_external(name):
    r = await call(DISP, "pair_code_create", member_name=name,
                   provider="codex", room="pack-main")
    st, resp = pair_post({"code": r["pair_code"]})
    assert st == 200, (name, st, resp)
    return resp["token"]


async def main():
    # ---------- 0. 工具面 ----------
    async with client(DISP) as c:
        tools = await c.list_tools()
    names = {t.name for t in tools}
    note("tools == 75", len(tools) == 75, f"got {len(tools)}")
    note("member_access_mode_set present", "member_access_mode_set" in names)

    # ---------- 1. 夹具 ----------
    ALICE = (await call(DISP, "member_register", name="alice"))["token"]
    BOB = (await call(DISP, "member_register", name="bob"))["token"]
    CAROL = (await call(DISP, "member_register", name="carol"))["token"]
    DAVE = (await call(DISP, "member_register", name="dave"))["token"]
    OWNER = (await call(DISP, "member_register", name="owner"))["token"]
    dbx("UPDATE members SET role='owner' WHERE name='owner'")
    GONE = (await call(DISP, "member_register", name="gone"))["token"]
    await call(DISP, "member_remove", name="gone")

    ml = await call(DISP, "member_list")
    rows = {m["name"]: m for m in ml["members"]}
    note("member_list: all rows carry access_mode",
         all("access_mode" in m for m in ml["members"]))
    note("member_list: fresh members read full",
         all(rows[n]["access_mode"] == "full"
             for n in ("alice", "bob", "carol", "dave", "dispatcher", "owner")))

    # ---------- 2. 设置面拒绝（alice 仍 full 时验 require 路径） ----------
    ok, err = await try_call(ALICE, "member_access_mode_set",
                             name="bob", mode="readonly")
    note("member calling set refused (dispatcher-only)",
         not ok and "dispatcher" in err, err[:70])
    ok, err = await try_call(DISP, "member_access_mode_set",
                             name="dispatcher", mode="readonly")
    note("set on dispatcher refused (anti-self-lock)",
         not ok and "dispatcher" in err, err[:70])
    ok, err = await try_call(DISP, "member_access_mode_set",
                             name="owner", mode="readonly")
    note("set on owner refused", not ok and "owner" in err, err[:70])
    ok, err = await try_call(DISP, "member_access_mode_set",
                             name="gone", mode="readonly")
    note("set on removed member refused", not ok and "removed" in err, err[:70])
    ok, err = await try_call(DISP, "member_access_mode_set",
                             name="nosuch", mode="readonly")
    note("set on missing member refused", not ok and "no such member" in err,
         err[:70])
    ok, err = await try_call(DISP, "member_access_mode_set",
                             name="alice", mode="bogus")
    note("set with bad mode refused", not ok and "bad access mode" in err,
         err[:70])
    note("no set audit written by rejected calls",
         audit_n("member_access_mode_set") == 0)
    note("alice still full after rejected sets",
         await mode_of(DISP, "alice") == "full")

    # ---------- 3. set 后立即生效（full 认领 -> readonly 被拒 -> full 恢复） ----------
    tc1 = (await call(DISP, "task_create", room="pack-main", title="carol-1",
                      body="b", assignee_name="carol"))["task_id"]
    await call(CAROL, "task_claim", task_id=tc1)
    note("carol (full) claim ok", True)
    out = await call(DISP, "member_access_mode_set", name="carol", mode="readonly")
    note("set returns {name, access_mode}",
         out == {"name": "carol", "access_mode": "readonly"}, str(out))
    note("set audit detail old -> new",
         audit_details("member_access_mode_set", "dispatcher")
         == ["carol: full -> readonly"],
         str(audit_details("member_access_mode_set", "dispatcher")))
    tc2 = (await call(DISP, "task_create", room="pack-main", title="carol-2",
                      body="b", assignee_name="carol"))["task_id"]
    ok, err = await try_call(CAROL, "task_claim", task_id=tc2)
    note("carol readonly claim denied immediately",
         not ok and "access mode" in err, err[:70])
    det = audit_details("access_mode_denied", "carol")
    note("denial audit has tool+mode",
         det == ["tool=task_claim mode=readonly"], str(det))
    await call(DISP, "member_access_mode_set", name="carol", mode="full")
    await call(CAROL, "task_claim", task_id=tc2)
    note("carol back to full claim ok", True)

    # ---------- 4. readonly battery（alice） ----------
    t1 = (await call(DISP, "task_create", room="pack-main", title="alice-1",
                     body="b", assignee_name="alice"))["task_id"]
    claim = await call(ALICE, "task_claim", task_id=t1)
    ctok = claim["claim_token"]
    await call(DISP, "member_access_mode_set", name="alice", mode="readonly")
    note("alice reads readonly in member_list",
         await mode_of(DISP, "alice") == "readonly")

    # 给 alice 指派一个 skill，使名单内的 skill_read 有可读对象。
    ok, err = await try_call(DISP, "skill_publish", name="ro-probe",
                             description="readonly probe", body="probe body")
    assert ok, err
    ok, err = await try_call(DISP, "skill_assign", name="ro-probe",
                             member_name="alice")
    assert ok, err
    # 备一张附件供名单内的 attachment_get 读取（图片魔数：一像素 PNG）。
    import base64 as _b64
    png1 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
    r = await call(DISP, "attachment_put", room="pack-main",
                   filename="probe.png", mime="image/png", data_base64=png1)
    att_id = r["attachment_id"]
    allowed = [
        ("heartbeat", {}),
        ("member_list", {}),
        ("room_list", {}),
        ("room_read_new", {"room": "pack-main", "since_id": 0, "limit": 5}),
        ("inbox_pull", {"room": "pack-main"}),
        ("task_list", {}),
        ("skill_list", {}),
        ("skill_read", {"name": "ro-probe"}),
        ("memory_read", {"scope": "private-core"}),
        ("attachment_list", {"room": "pack-main"}),
        ("attachment_get", {"attachment_id": att_id}),
        ("desktop_status", {}),
        ("room_summary_get", {"room": "pack-main"}),
    ]
    bad = []
    for tool, kw in allowed:
        ok, err = await try_call(ALICE, tool, **kw)
        if not ok:
            bad.append(f"{tool}: {err[:60]}")
    note("readonly: 13 member-reachable listed tools all usable", not bad,
         "; ".join(bad))
    # 名单内但本体另有 dispatcher 门的工具：模式闸应放行（名单内）、由
    # require 以 role 拒之——错误不得是模式闸的、且不写 access_mode_denied。
    role_gated = [
        ("task_audit", {"task_id": t1}),
        ("task_transfers_list", {"task_id": t1}),
        ("run_list", {"task_id": t1}),
        ("rule_list", {}),
        ("egress_rule_list", {}),
        ("template_list", {}),
        ("pair_code_list", {}),
    ]
    base_rg = audit_n("access_mode_denied", "alice")
    bad = []
    for tool, kw in role_gated:
        ok, err = await try_call(ALICE, tool, **kw)
        if ok or "requires role" not in err or "access mode" in err:
            bad.append(f"{tool}: ok={ok} err={err[:60]}")
    note("readonly: 7 listed-but-role-gated tools refused by require, not mode gate",
         not bad and audit_n("access_mode_denied", "alice") == base_rg,
         "; ".join(bad))

    t2 = (await call(DISP, "task_create", room="pack-main", title="alice-2",
                     body="b", assignee_name="alice"))["task_id"]
    denied = [
        ("task_claim", {"task_id": t2}),
        ("room_post", {"room": "pack-main", "body": "should not land"}),
        ("inbox_mark_read", {"up_to_id": 1, "room": "pack-main"}),
        ("attachment_put", {"room": "pack-main", "filename": "x.png",
                            "mime": "image/png", "data_base64": png64()}),
        ("task_complete", {"task_id": t1, "claim_token": ctok, "result": "x"}),
    ]
    base = audit_n("access_mode_denied", "alice")
    for tool, kw in denied:
        ok, err = await try_call(ALICE, tool, **kw)
        note(f"readonly: {tool} denied by mode gate",
             not ok and "access mode" in err, err[:70])
    det = audit_details("access_mode_denied", "alice")[base:]
    note("readonly: one denial audit per denied tool, with tool+mode",
         det == [f"tool={t} mode=readonly" for t, _ in denied], str(det))
    hb = await call(ALICE, "heartbeat")
    note("readonly: heartbeat exception works", hb.get("ok") is True)
    gone_msg = dbq("SELECT COUNT(*) AS n FROM messages WHERE body='should not land'")[0]["n"]
    note("readonly: denied room_post wrote nothing", gone_msg == 0)

    # ---------- 5. workspace battery（alice） ----------
    await call(DISP, "member_access_mode_set", name="alice", mode="workspace")
    await call(ALICE, "task_claim", task_id=t2)
    note("workspace: task_claim ok", True)
    await call(ALICE, "room_post", room="pack-main", body="workspace can talk")
    note("workspace: room_post ok", True)
    base = audit_n("access_mode_denied", "alice")
    ok, err = await try_call(ALICE, "egress_check", url="https://example.com")
    note("workspace: egress_check denied", not ok and "access mode" in err,
         err[:70])
    ok, err = await try_call(ALICE, "skill_publish", name="ws-probe", body="x")
    note("workspace: skill_publish denied", not ok and "access mode" in err,
         err[:70])
    ok, err = await try_call(ALICE, "skill_assign",
                             name="ws-probe", member_name="alice")
    note("workspace: skill_assign denied", not ok and "access mode" in err,
         err[:70])
    det = audit_details("access_mode_denied", "alice")[base:]
    note("workspace: denial audits carry mode=workspace",
         det == ["tool=egress_check mode=workspace",
                 "tool=skill_publish mode=workspace",
                 "tool=skill_assign mode=workspace"], str(det))
    await call(DISP, "member_access_mode_set", name="alice", mode="full")
    await call(ALICE, "room_post", room="pack-main", body="alice full again")
    note("alice restored to full, writes ok", True)

    # ---------- 6. external + readonly 双闸叠加 ----------
    EXT = await make_external("ext-ro")
    te = (await call(DISP, "task_create", room="pack-main", title="ext task",
                     body="b", assignee_name="ext-ro"))["task_id"]
    await call(DISP, "member_access_mode_set", name="ext-ro", mode="readonly")
    ext_denied0 = audit_n("external_tool_denied", "ext-ro")
    mode_denied0 = audit_n("access_mode_denied", "ext-ro")
    ok, err = await try_call(EXT, "task_claim", task_id=te)
    note("ext+readonly: task_claim (whitelisted) denied by mode gate",
         not ok and "access mode" in err, err[:70])
    note("ext+readonly: mode audit +1, whitelist audit +0 (no double write)",
         audit_n("access_mode_denied", "ext-ro") == mode_denied0 + 1
         and audit_n("external_tool_denied", "ext-ro") == ext_denied0)
    ok, err = await try_call(EXT, "room_summary_set", room="pack-main",
                             summary="{}")
    note("ext+readonly: room_summary_set (not whitelisted) denied by whitelist gate",
         not ok and "external" in err, err[:70])
    note("ext+readonly: whitelist audit +1, mode audit +0 (no double write)",
         audit_n("external_tool_denied", "ext-ro") == ext_denied0 + 1
         and audit_n("access_mode_denied", "ext-ro") == mode_denied0 + 1)
    ok, err = await try_call(EXT, "inbox_pull")
    note("ext+readonly: inbox_pull passes both gates", ok, err[:70] if not ok else "")
    hb = await call(EXT, "heartbeat")
    note("ext+readonly: heartbeat passes both gates", hb.get("ok") is True)
    await call(DISP, "member_access_mode_set", name="ext-ro", mode="full")
    await call(EXT, "task_claim", task_id=te)
    note("ext back to full: claim ok", True)

    # ---------- 7. 脏值 / NULL 归一 ----------
    dbx("UPDATE members SET access_mode='weird' WHERE name='bob'")
    note("dirty access_mode value reads as full",
         await mode_of(DISP, "bob") == "full")
    await call(BOB, "room_post", room="pack-main", body="bob still writes")
    note("dirty access_mode behaves as full", True)
    dbx("UPDATE members SET access_mode=NULL WHERE name='bob'")
    note("NULL access_mode reads as full", await mode_of(DISP, "bob") == "full")

    # ---------- 8. 并发探针：set 与调用交错 ----------
    for run in (1, 2):
        await call(DISP, "member_access_mode_set", name="dave", mode="full")
        outcomes = []

        def setter(k):
            async def go():
                for i in range(15):
                    m = "readonly" if (i + k) % 2 else "full"
                    try:
                        await call(DISP, "member_access_mode_set",
                                   name="dave", mode=m)
                        outcomes.append("set-ok")
                    except Exception as e:
                        outcomes.append(f"set-err:{str(e)[:50]}")
            asyncio.run(go())

        def caller(k):
            async def go():
                for i in range(20):
                    try:
                        await call(DAVE, "room_post", room="pack-main",
                                   body=f"conc {k}-{i}")
                        outcomes.append("post-ok")
                    except Exception as e:
                        s = str(e)
                        outcomes.append("post-denied" if "access mode" in s
                                        else f"post-err:{s[:50]}")
                    try:
                        await call(DAVE, "task_list")
                        outcomes.append("list-ok")
                    except Exception as e:
                        outcomes.append(f"list-err:{str(e)[:50]}")
            asyncio.run(go())

        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = [ex.submit(setter, k) for k in range(3)]
            futs += [ex.submit(caller, k) for k in range(3)]
            for f in futs:
                f.result()
        unexpected = [o for o in outcomes if "err:" in o]
        n_set = sum(1 for o in outcomes if o == "set-ok")
        n_post_ok = sum(1 for o in outcomes if o == "post-ok")
        n_post_denied = sum(1 for o in outcomes if o == "post-denied")
        n_list = sum(1 for o in outcomes if o == "list-ok")
        note(f"concurrency run{run}: no unexpected errors "
             f"(set-ok={n_set} post-ok={n_post_ok} post-denied={n_post_denied} "
             f"list-ok={n_list})",
             not unexpected and n_set == 45 and n_list == 60
             and n_post_ok + n_post_denied == 60,
             "; ".join(unexpected[:3]))
        m = await mode_of(DISP, "dave")
        note(f"concurrency run{run}: final mode is a valid value", m in
             ("full", "workspace", "readonly"), f"mode={m}")
    await call(DISP, "member_access_mode_set", name="dave", mode="full")
    hb = await call(DISP, "heartbeat")
    note("server healthy after concurrency probe", hb.get("ok") is True)

    print(f"== {sum(res)}/{len(res)} PASS")
    sys.exit(0 if all(res) else 1)


asyncio.run(main())
