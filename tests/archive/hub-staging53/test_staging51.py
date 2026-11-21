import asyncio, base64, json, os, sqlite3, subprocess, sys, time
sys.path.insert(0, ".")
from harness import call, try_call, client, pair_post, DISP, DB, HERE

res = []
SECRETS = []  # 全部明文码/token，结尾做秘密卫生扫描


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


def png_bytes(n):
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * (n - 8)


def b64(data):
    return base64.b64encode(data).decode()


def es(err):
    return err[:80] if isinstance(err, str) else ""


async def make_external(name, provider="codex", room="pack-main", display_name="", reclaim=False):
    r = await call(DISP, "pair_code_create", member_name=name, provider=provider,
                   room=room, display_name=display_name, reclaim=reclaim)
    SECRETS.append(r["pair_code"])
    st, resp = pair_post({"code": r["pair_code"]})
    assert st == 200, (name, st, resp)
    SECRETS.append(resp["token"])
    return r, resp


async def main():
    # ---------- 0. 工具面 ----------
    async with client(DISP) as c:
        tools = await c.list_tools()
    names = {t.name for t in tools}
    note("tools == 74", len(tools) == 74, f"got {len(tools)}")
    note("new tools present", {"pair_code_create", "pair_code_list", "pair_code_revoke",
                               "member_token_rotate", "external_gate", "member_rename",
                               "inbox_pull", "inbox_mark_read", "inbox_send"} <= names)

    # ---------- 1. 夹具：内部成员、房间、入群前内容 ----------
    reg = await call(DISP, "member_register", name="alice")
    ALICE = reg["token"]; SECRETS.append(ALICE)
    reg = await call(DISP, "member_register", name="bob")
    BOB = reg["token"]; SECRETS.append(BOB)
    reg = await call(DISP, "member_register", name="owner")
    OWNER = reg["token"]; SECRETS.append(OWNER)
    await call(DISP, "room_create", name="room-b")
    m1 = await call(DISP, "room_post", room="pack-main", kind="chat", body="入群前消息一")
    m2 = await call(DISP, "room_post", room="pack-main", kind="chat", body="入群前消息二")
    await call(DISP, "room_post", room="room-b", kind="chat", body="room-b 的消息")
    pre_att = await call(ALICE, "attachment_put", room="pack-main", filename="pre.png",
                         mime="image/png", data_base64=b64(png_bytes(64)))
    roomb_att = await call(DISP, "attachment_put", room="room-b", filename="rb.png",
                           mime="image/png", data_base64=b64(png_bytes(64)))
    main_room_id = dbq("SELECT id FROM rooms WHERE name='pack-main'")[0]["id"]
    pre_join_max = dbq("SELECT COALESCE(MAX(id),0) AS m FROM messages WHERE room_id=?",
                       (main_room_id,))[0]["m"]
    note("fixture: pre-join max message id", pre_join_max == m2["message_id"],
         f"max={pre_join_max} m2={m2['message_id']}")

    # ---------- 2. 配对全链 ----------
    ok, err = await try_call(DISP, "pair_code_create", member_name="alice",
                             provider="codex", room="pack-main")
    note("pair create for internal name rejected", not ok and "internal member" in err, es(err))
    ok, err = await try_call(DISP, "pair_code_create", member_name="owner",
                             provider="codex", room="pack-main")
    note("pair create for owner rejected", not ok and "internal member" in err, es(err))
    ok, err = await try_call(DISP, "pair_code_create", member_name="bad name",
                             provider="codex", room="pack-main")
    note("pair create bad name rejected", not ok, es(err))
    ok, err = await try_call(DISP, "pair_code_create", member_name="zz-top",
                             provider="bogus", room="pack-main")
    note("pair create bad provider rejected", not ok, es(err))
    ok, err = await try_call(DISP, "pair_code_create", member_name="zz-top",
                             provider="codex", room="no-such-room")
    note("pair create bad room rejected", not ok, es(err))

    pc = await call(DISP, "pair_code_create", member_name="codex-a", provider="codex",
                    room="pack-main", display_name="群主的 Codex")
    CODE_A = pc["pair_code"]; SECRETS.append(CODE_A)
    lst = await call(DISP, "pair_code_list")
    row = [x for x in lst["pair_codes"] if x["code_id"] == pc["code_id"]][0]
    note("pair_code_list shows unused, no secret material",
         row["status"] == "unused" and "pair_code" not in row and "code_hash" not in row)
    st, resp = pair_post({"code": "pk-pair-definitely-wrong"})
    note("wrong code -> 400 generic", st == 400 and resp.get("error") == "invalid pair code"
         and "definitely-wrong" not in json.dumps(resp))
    st, resp = pair_post(None, raw=b"{not json")
    note("garbage payload -> 400", st == 400)
    st, resp = pair_post({"code": CODE_A})
    note("redeem ok", st == 200 and resp.get("member") == "codex-a", f"st={st}")
    EXT_A = resp["token"]; SECRETS.append(EXT_A)
    EXT_A_ID = resp["member_id"]
    mrow = dbq("SELECT * FROM members WHERE name='codex-a'")[0]
    note("external member row fields",
         mrow["kind"] == "external" and mrow["bound_room_id"] == main_room_id
         and mrow["provider"] == "codex" and mrow["display_name"] == "群主的 Codex"
         and mrow["role"] == "member")
    note("joined_msg_id == pre-join max", mrow["joined_msg_id"] == pre_join_max,
         f"joined={mrow['joined_msg_id']} max={pre_join_max}")
    av = json.loads(mrow["avatar"])
    note("avatar combo auto-assigned", bool(av.get("combo")))
    cur = dbq("SELECT cursor_id FROM member_read_cursors WHERE member_id=? AND room_id=?",
              (EXT_A_ID, main_room_id))
    note("read cursor initialized at watermark", len(cur) == 1 and cur[0]["cursor_id"] == pre_join_max)
    st, resp2 = pair_post({"code": CODE_A})
    note("second redeem of same code -> 400", st == 400)
    ok, hb = await try_call(EXT_A, "heartbeat")
    note("external token heartbeat ok", ok and hb.get("member") == "codex-a")

    # ---------- 3. 未认领巡检：external 宽限放宽（须在任何内部额度/心跳夹具之前） ----------
    _, pat = await make_external("ext-patrol")
    PAT_ID = pat["member_id"]
    t_pat = (await call(DISP, "task_create", room="pack-main", title="patrol ext",
                        body="b", assignee_name="ext-patrol"))["task_id"]
    t_ali = (await call(DISP, "task_create", room="pack-main", title="patrol alice",
                        body="b", assignee_name="alice"))["task_id"]
    old = time.time() - 600
    dbx("UPDATE tasks SET updated_at=? WHERE id IN (?,?)", (old, t_pat, t_ali))
    subprocess.run([sys.executable, "hub.py", "sweep"], cwd=HERE,
                   capture_output=True, timeout=60)
    esc_pat = dbq("SELECT COUNT(*) AS n FROM audit WHERE action='task_unclaimed_escalate'"
                  " AND detail LIKE ?", (f"task {t_pat} %",))[0]["n"]
    esc_ali = dbq("SELECT COUNT(*) AS n FROM audit WHERE action='task_unclaimed_escalate'"
                  " AND detail LIKE ?", (f"task {t_ali} %",))[0]["n"]
    note("patrol: internal assignee escalated after 240s grace", esc_ali >= 1)
    note("patrol: external assignee NOT escalated at 600s (grace 2700s)", esc_pat == 0)
    dbx("UPDATE tasks SET updated_at=? WHERE id=?", (time.time() - 3000, t_pat))
    subprocess.run([sys.executable, "hub.py", "sweep"], cwd=HERE,
                   capture_output=True, timeout=60)
    esc_pat = dbq("SELECT COUNT(*) AS n FROM audit WHERE action='task_unclaimed_escalate'"
                  " AND detail LIKE ?", (f"task {t_pat} %",))[0]["n"]
    trow = dbq("SELECT status, assignee FROM tasks WHERE id=?", (t_pat,))[0]
    note("patrol: external escalated after 2700s grace", esc_pat >= 1)
    note("patrol: escalated external task stays assigned (no relay to external)",
         trow["status"] == "open" and trow["assignee"] == PAT_ID)

    # ---------- 4. 白名单矩阵（external 调名单外工具全拒 + 审计） ----------
    banned = [
        ("memory_read", dict(scope="private-core")),
        ("memory_write", dict(scope="private", key="x", text="y")),
        ("memory_versions", dict(scope="private", key="x")),
        ("memory_rollback", dict(scope="private", key="x", version_id=1)),
        ("skill_list", {}), ("skill_read", dict(name="nope")),
        ("skill_publish", dict(name="s", body="b")),
        ("skill_assign", dict(name="s", member_name="alice")),
        ("skill_unassign", dict(name="s", member_name="alice")),
        ("skill_rollback", dict(name="s", version=1)),
        ("task_create", dict(room="pack-main", title="x", body="y")),
        ("task_schedule", dict(room="pack-main", title="t", body="b",
                               member_name="alice", at_epoch=9999999999)),
        ("task_review", dict(task_id=1, approve=True)),
        ("task_cancel", dict(task_id=1)),
        ("task_audit", dict(task_id=1)),
        ("task_set_urgent", dict(task_id=1, urgent=True)),
        ("task_clear_backoff", dict(task_id=1)),
        ("task_release", dict(task_id=1)),
        ("task_transfer", dict(task_id=1, claim_token="x", to_member="alice")),
        ("task_accept_transfer", dict(task_id=1)),
        ("task_transfers_list", dict(task_id=1)),
        ("desktop_acquire", dict(task_id=1, claim_token="x")),
        ("desktop_release", {}), ("desktop_status", {}),
        ("room_create", dict(name="zzz")),
        ("room_rename", dict(room="room-b", new_name="room-c")),
        ("room_archive", dict(room="room-b")),
        ("room_summary_set", dict(room="pack-main", summary="{}")),
        ("rule_list", {}), ("rule_upsert", dict(action="", target="", effect="allow")),
        ("rule_delete", dict(rule_id=1)),
        ("egress_rule_list", {}),
        ("egress_rule_upsert", dict(pattern="example.com", effect="allow")),
        ("egress_rule_delete", dict(rule_id=1)),
        ("review_file", dict(task_id=1, target_url="https://x.com",
                             owner_quote="q", member_desc="d", verdict="allow")),
        ("member_register", dict(name="zzz")),
        ("member_remove", dict(name="alice")),
        ("member_avatar_set", dict(name="alice", avatar_combo="{}")),
        ("member_quota_set", dict(member_name="alice", week_used_pct=1)),
        ("member_quota_report", dict(week_used_pct=1)),
        ("member_login_state_set", dict(name="alice", state="pending")),
        ("pair_code_create", dict(member_name="zz", provider="codex", room="pack-main")),
        ("pair_code_list", {}), ("pair_code_revoke", dict(code_id=1)),
        ("member_token_rotate", dict(name="alice")),
        ("external_gate", {}),
        ("member_rename", dict(name="alice", display_name="x")),
        ("run_list", {}),
        ("template_list", {}),
        ("template_upsert", dict(name="t", title_tpl="t", body_tpl="b")),
        ("template_delete", dict(name="t")), ("template_fire", dict(name="t")),
    ]
    before = audit_n("external_tool_denied", "codex-a")
    all_denied = True
    for tool, kw in banned:
        ok, err = await try_call(EXT_A, tool, **kw)
        if ok:
            all_denied = False
            print(f"  !! NOT DENIED: {tool} -> {err}", flush=True)
    after = audit_n("external_tool_denied", "codex-a")
    note(f"whitelist: all {len(banned)} banned tools denied for external", all_denied)
    note("whitelist: every denial audited", after - before == len(banned),
         f"delta={after - before}")
    arow = dbq("SELECT removed_at FROM members WHERE name='alice'")[0]
    rrow = dbq("SELECT archived_at, name FROM rooms WHERE id=(SELECT id FROM rooms WHERE name='room-b')")[0]
    note("whitelist denials caused no side effects",
         arow["removed_at"] is None and rrow["archived_at"] is None and rrow["name"] == "room-b")
    # 内部成员调调度员工具仍按角色拒（既有行为回归）
    ok, err = await try_call(ALICE, "pair_code_create", member_name="zz",
                             provider="codex", room="pack-main")
    note("internal member still role-blocked from dispatcher tools", not ok)
    ok, err = await try_call(ALICE, "task_create", room="pack-main", title="x", body="y")
    note("internal member still cannot task_create", not ok)

    # ---------- 5. 房间隔离 + 水位（codex-a 绑定 pack-main） ----------
    rl = await call(EXT_A, "room_list")
    note("room_list for external only bound room",
         [r["name"] for r in rl["rooms"]] == ["pack-main"])
    ok, err = await try_call(EXT_A, "room_read_new", room="room-b")
    note("external room_read_new other room rejected", not ok)
    ok, err = await try_call(EXT_A, "room_post", room="room-b", kind="chat", body="x")
    note("external room_post other room rejected", not ok)
    ok, err = await try_call(EXT_A, "room_summary_get", room="room-b")
    note("external room_summary_get other room rejected", not ok)
    ok, _ = await try_call(EXT_A, "room_summary_get", room="pack-main")
    note("external room_summary_get bound room ok", ok)
    msgs = await call(EXT_A, "room_read_new", room="pack-main", since_id=0, limit=200)
    ids = [x["id"] for x in msgs["messages"]]
    bodies = [x["body"] for x in msgs["messages"]]
    note("watermark: since_id=0 returns only post-join messages",
         all(i > pre_join_max for i in ids) and "入群前消息一" not in bodies
         and "入群前消息二" not in bodies, f"ids={ids[:5]}")
    msgs_t = await call(EXT_A, "room_read_new", room="pack-main", since_id=0,
                        limit=200, tail=True)
    note("watermark: tail=True also truncated",
         all(x["id"] > pre_join_max for x in msgs_t["messages"]))
    ok, err = await try_call(EXT_A, "attachment_get", attachment_id=pre_att["attachment_id"])
    note("watermark: pre-join attachment_get rejected", not ok, es(err))
    ok, err = await try_call(EXT_A, "attachment_get", attachment_id=roomb_att["attachment_id"])
    note("isolation: other-room attachment_get rejected", not ok)
    post_att = await call(ALICE, "attachment_put", room="pack-main", filename="post.png",
                          mime="image/png", data_base64=b64(png_bytes(64)))
    ok, got = await try_call(EXT_A, "attachment_get", attachment_id=post_att["attachment_id"])
    note("post-join attachment_get ok", ok and got["attachment_id"] == post_att["attachment_id"])
    alist = await call(EXT_A, "attachment_list", room="pack-main")
    al_ids = [a["id"] for a in alist["attachments"]]
    note("watermark: attachment_list hides pre-join, shows post-join",
         pre_att["attachment_id"] not in al_ids and post_att["attachment_id"] in al_ids)
    ok, err = await try_call(EXT_A, "attachment_list", room="room-b")
    note("external attachment_list other room rejected", not ok)
    ok, err = await try_call(EXT_A, "attachment_put", room="room-b", filename="x.png",
                             mime="image/png", data_base64=b64(png_bytes(64)))
    note("external attachment_put other room rejected", not ok)
    ml = await call(EXT_A, "member_list")
    keys0 = set(ml["members"][0].keys())
    note("member_list sanitized for external",
         keys0 == {"name", "display_name", "role", "kind", "provider",
                   "avatar", "last_heartbeat"}, f"keys={sorted(keys0)}")
    ml_d = await call(DISP, "member_list")
    da = [m for m in ml_d["members"] if m["name"] == "alice"][0]
    de = [m for m in ml_d["members"] if m["name"] == "codex-a"][0]
    note("member_list new fields (dispatcher view)",
         da["kind"] == "internal" and da["display_name"] == "alice"
         and da["bound_room_id"] is None and "week_done" in da
         and de["kind"] == "external" and de["bound_room_id"] == main_room_id)

    # task_list 只回本人
    t_alice = (await call(DISP, "task_create", room="pack-main", title="for alice",
                          body="secret-for-alice", assignee_name="alice"))["task_id"]
    t_open = (await call(DISP, "task_create", room="pack-main", title="unassigned",
                         body="secret-open", assignee_name=""))["task_id"]
    t_ext = (await call(DISP, "task_create", room="pack-main", title="for ext",
                        body="b", assignee_name="codex-a"))["task_id"]
    tl = await call(EXT_A, "task_list")
    tl_ids = [t["id"] for t in tl["tasks"]]
    note("task_list for external: only own tasks",
         t_ext in tl_ids and t_alice not in tl_ids and t_open not in tl_ids,
         f"ids={tl_ids}")
    tl2 = await call(EXT_A, "task_list", room="room-b", assignee_name="alice")
    note("task_list for external: caller filters overridden",
         all(t["id"] == t_ext or t["assignee"] == "codex-a" for t in tl2["tasks"])
         and t_alice not in [t["id"] for t in tl2["tasks"]])

    # ---------- 6. 任务全链 + 认领限制 + 伪造探针 ----------
    ok, err = await try_call(EXT_A, "task_claim", task_id=t_alice)
    note("claim: external cannot claim other's task", not ok)
    ok, err = await try_call(EXT_A, "task_claim", task_id=t_open)
    note("claim: external cannot claim unassigned task", not ok)
    cl = await call(EXT_A, "task_claim", task_id=t_ext)
    EXT_TOK = cl["claim_token"]
    rs = await call(EXT_A, "run_start", task_id=t_ext, claim_token=EXT_TOK)
    note("run_start ok for external holder", rs["status"] == "running")
    upd = await call(EXT_A, "task_update", task_id=t_ext, claim_token=EXT_TOK,
                     note="进度：开始处理")
    note("task_update ok", upd["status"] == "working")
    comp = await call(EXT_A, "task_complete", task_id=t_ext, claim_token=EXT_TOK,
                      result="做完了")
    note("task_complete -> review", comp["status"] == "review")
    rev = await call(DISP, "task_review", task_id=t_ext, approve=True)
    note("dispatcher review closes external task", rev["status"] == "done")

    # 引擎标记卡：task_create 侧永久拒绝（标题标记 + 正文标记 + 跨房）
    ok, err = await try_call(DISP, "task_create", room="pack-main", title="[ENG] 截图",
                             body="b", assignee_name="codex-a")
    note("task_create pinned(title) to external -> permanent reject",
         not ok and "permanent:" in err, es(err))
    ok, err = await try_call(DISP, "task_create", room="pack-main", title="普通标题",
                             body="正文里有 [M4A] 标记", assignee_name="codex-a")
    note("task_create pinned(body) to external -> permanent reject",
         not ok and "permanent:" in err, es(err))
    ok, err = await try_call(DISP, "task_create", room="room-b", title="跨房卡",
                             body="b", assignee_name="codex-a")
    note("task_create cross-room to external -> permanent reject",
         not ok and "permanent:" in err, es(err))
    # claim 侧独立防线：DB 直改一张已指派卡为引擎标记
    t_pin = (await call(DISP, "task_create", room="pack-main", title="pin me",
                        body="b", assignee_name="codex-a"))["task_id"]
    dbx("UPDATE tasks SET body='正文 [ENG] 标记' WHERE id=?", (t_pin,))
    ok, err = await try_call(EXT_A, "task_claim", task_id=t_pin)
    note("claim: external cannot claim executor-pinned task", not ok)
    await call(DISP, "task_cancel", task_id=t_pin)
    # block / release / 再认领口径与既有成员一致
    t_blk = (await call(DISP, "task_create", room="pack-main", title="block me",
                        body="b", assignee_name="codex-a"))["task_id"]
    cl = await call(EXT_A, "task_claim", task_id=t_blk)
    blk = await call(EXT_A, "task_block", task_id=t_blk, claim_token=cl["claim_token"],
                     reason="缺输入", fail_class="task")
    note("task_block ok", blk["status"] == "blocked" and blk["fail_class"] == "task")
    rel = await call(DISP, "task_release", task_id=t_blk, clear_backoff=True)
    note("task_release reopens blocked external task", rel["status"] == "open")
    cl = await call(EXT_A, "task_claim", task_id=t_blk)
    note("re-claim after release ok", cl["status"] == "claimed")
    await call(EXT_A, "task_complete", task_id=t_blk, claim_token=cl["claim_token"],
               result="补齐了")
    await call(DISP, "task_review", task_id=t_blk, approve=True)

    # 伪造与借令牌探针
    ok, err = await try_call(EXT_A, "room_post", room="pack-main", kind="task_card", body="任务#999 假卡")
    note("forge: external task_card via room_post rejected", not ok)
    ok, err = await try_call(EXT_A, "room_post", room="pack-main", kind="decision", body="批准 任务#1")
    note("forge: external decision via room_post rejected", not ok)
    ok, err = await try_call(ALICE, "room_post", room="pack-main", kind="task_card", body="任务#999 假卡")
    note("forge: internal task_card via room_post rejected", not ok)
    ok, err = await try_call(ALICE, "room_post", room="pack-main", kind="decision", body="批准 任务#1")
    note("forge: internal decision via room_post rejected", not ok)
    t_borrow = (await call(DISP, "task_create", room="pack-main", title="alice task",
                           body="b", assignee_name="alice"))["task_id"]
    cl_a = await call(ALICE, "task_claim", task_id=t_borrow)
    ok, err = await try_call(EXT_A, "task_update", task_id=t_borrow,
                             claim_token=cl_a["claim_token"], note="偷更新")
    note("forge: borrowed claim_token rejected (external)", not ok)
    t_own = (await call(DISP, "task_create", room="pack-main", title="ext task 2",
                        body="b", assignee_name="codex-a"))["task_id"]
    cl_e = await call(EXT_A, "task_claim", task_id=t_own)
    ok, err = await try_call(ALICE, "task_complete", task_id=t_own,
                             claim_token=cl_e["claim_token"], result="冒交")
    note("forge: borrowed claim_token rejected (internal)", not ok)
    await call(EXT_A, "task_block", task_id=t_own, claim_token=cl_e["claim_token"],
               reason="收尾", fail_class="task")
    await call(DISP, "task_cancel", task_id=t_own)
    await call(ALICE, "task_complete", task_id=t_borrow,
               claim_token=cl_a["claim_token"], result="alice 交活")
    await call(DISP, "task_review", task_id=t_borrow, approve=True)
    # 消息编辑/删除：本人 + 本房
    mp = await call(EXT_A, "room_post", room="pack-main", kind="chat", body="ext 的消息")
    ed = await call(EXT_A, "message_edit", message_id=mp["message_id"], new_body="改过了")
    note("external edits own message ok", ed["edited"] is True)
    dl = await call(EXT_A, "message_delete", message_id=mp["message_id"])
    note("external deletes own message ok", dl["deleted"] is True)
    ok, err = await try_call(EXT_A, "message_edit", message_id=m1["message_id"], new_body="x")
    note("external cannot edit others' message", not ok)

    # ---------- 7. 频控：三窗口 / approval 硬顶 / note ----------
    _, rt = await make_external("ext-rate")
    RT = rt["token"]; SECRETS.append(RT); RT_ID = rt["member_id"]
    allok = True
    for i in range(5):
        ok, _ = await try_call(RT, "room_post", room="pack-main", kind="chat", body=f"r{i}")
        allok = allok and ok
    note("rate: first 5 posts in a minute ok", allok)
    ok, err = await try_call(RT, "room_post", room="pack-main", kind="chat", body="r6")
    note("rate: 6th post in a minute hard-rejected with retry_after",
         not ok and "rate_limited" in err and "retry_after" in err, es(err))
    for batch in range(3):
        dbx("UPDATE messages SET created_at=created_at-180 WHERE author_id=?", (RT_ID,))
        for i in range(5):
            ok, err = await try_call(RT, "room_post", room="pack-main", kind="chat",
                                     body=f"b{batch}-{i}")
            if not ok:
                note(f"rate: hour-window batch {batch} post failed early", False, es(err))
    dbx("UPDATE messages SET created_at=created_at-180 WHERE author_id=?", (RT_ID,))
    ok, err = await try_call(RT, "room_post", room="pack-main", kind="chat", body="over-hour")
    note("rate: 21st post within an hour rejected (hour window)", not ok and "rate_limited" in err)
    note("rate: rejections audited", audit_n("external_rate_limited", "ext-rate") >= 2)
    ok, _ = await try_call(DISP, "room_post", room="pack-main", kind="chat", body="调度员不受限")
    note("rate: dispatcher unaffected", ok)

    _, dy = await make_external("ext-day")
    DY_ID = dy["member_id"]
    now0 = time.time()
    conn = sqlite3.connect(DB)
    conn.executemany(
        "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at)"
        " VALUES(?,?,'chat','synthetic',NULL,?)",
        [(main_room_id, DY_ID, now0 - 3700)] * 100)
    conn.commit(); conn.close()
    ok, err = await try_call(dy["token"], "room_post", room="pack-main", kind="chat", body="over-day")
    note("rate: 101st post within a day rejected (day window)", not ok and "rate_limited" in err)

    _, ap = await make_external("ext-appr")
    AP = ap["token"]; SECRETS.append(AP)
    appr_tasks = []
    for i in range(3):
        tid = (await call(DISP, "task_create", room="pack-main", title=f"appr {i}",
                          body="b", assignee_name="ext-appr"))["task_id"]
        appr_tasks.append(tid)
    cl = await call(AP, "task_claim", task_id=appr_tasks[0])
    allok = True
    for i in range(5):
        ok, _ = await try_call(AP, "room_post", room="pack-main", kind="approval_request",
                               body=f"任务#{appr_tasks[0]} 需要批准 第{i+1}次")
        allok = allok and ok
    note("approval: 5 requests on one task ok", allok)
    ok, err = await try_call(AP, "room_post", room="pack-main", kind="approval_request",
                             body=f"任务#{appr_tasks[0]} 需要批准 第6次")
    note("approval: 6th request on same task rejected (per-task cap)",
         not ok and "approval_request_cap" in err and '"task"' in err, es(err))
    cl = await call(AP, "task_claim", task_id=appr_tasks[1])
    allok = True
    for i in range(5):
        ok, _ = await try_call(AP, "room_post", room="pack-main", kind="approval_request",
                               body=f"任务#{appr_tasks[1]} 需要批准 第{i+1}次")
        allok = allok and ok
    note("approval: 5 more on second task ok (10/hour)", allok)
    cl = await call(AP, "task_claim", task_id=appr_tasks[2])
    ok, err = await try_call(AP, "room_post", room="pack-main", kind="approval_request",
                             body=f"任务#{appr_tasks[2]} 需要批准")
    note("approval: 11th request within an hour rejected (member cap)",
         not ok and "approval_request_cap" in err and "member_hour" in err, es(err))
    note("approval: caps audited", audit_n("external_approval_cap", "ext-appr") >= 2)
    ok, _ = await try_call(AP, "room_post", room="pack-main", kind="chat", body="普通消息不受 approval 顶影响")
    note("approval: ordinary chat not blocked by approval cap", ok)
    ok, err = await try_call(AP, "room_post", room="pack-main", kind="approval_request",
                             body=f"任务#{t_alice} 需要批准")
    note("approval: request for other's task rejected (existing guard)", not ok)

    _, nt = await make_external("ext-note")
    NT = nt["token"]; SECRETS.append(NT)
    t_note = (await call(DISP, "task_create", room="pack-main", title="note task",
                         body="b", assignee_name="ext-note"))["task_id"]
    cl = await call(NT, "task_claim", task_id=t_note)
    NT_TOK = cl["claim_token"]
    ok, err = await try_call(NT, "task_update", task_id=t_note, claim_token=NT_TOK,
                             note="x" * 4001)
    note("note: 4001 chars rejected for external", not ok and "4000" in err, es(err))
    ok, _ = await try_call(NT, "task_update", task_id=t_note, claim_token=NT_TOK,
                           note="x" * 4000)
    note("note: 4000 chars ok", ok)
    for i in range(4):
        await call(NT, "task_update", task_id=t_note, claim_token=NT_TOK, note=f"n{i}")
    ok, err = await try_call(NT, "task_update", task_id=t_note, claim_token=NT_TOK, note="n6")
    note("note: 6th note within a minute rate-limited", not ok and "rate_limited" in err)
    ok, err = await try_call(NT, "room_post", room="pack-main", kind="chat", body="同窗口")
    note("note: notes share the message rate window", not ok and "rate_limited" in err)
    ok, _ = await try_call(NT, "task_update", task_id=t_note, claim_token=NT_TOK, note="")
    note("note: empty note (lease renew) not rate-limited", ok)

    # ---------- 8. external 附件独立配额 ----------
    # 注：MCP 传输层有 4MiB 请求体上限（SDK 默认），>~3MB 的文件经 HTTP 到
    # 不了工具层；hub 侧 5MB/50MB 精确口径在 test_direct.py 进程内直测。
    _, a1 = await make_external("ext-att1")
    A1 = a1["token"]; SECRETS.append(A1)
    ok, err = await try_call(A1, "attachment_put", room="pack-main", filename="big.png",
                             mime="image/png", data_base64=b64(png_bytes(5_500_000)))
    note("attach: external >5MB upload never succeeds", not ok, es(err))
    ok, _ = await try_call(A1, "attachment_put", room="pack-main", filename="ok3.png",
                           mime="image/png", data_base64=b64(png_bytes(3_000_000)))
    note("attach: external 3MB ok over HTTP", ok)
    ok, _ = await try_call(ALICE, "attachment_put", room="pack-main", filename="two.png",
                           mime="image/png", data_base64=b64(png_bytes(2_000_000)))
    note("attach: internal 2MB ok over HTTP", ok)
    _, a2 = await make_external("ext-att2")
    A2 = a2["token"]; SECRETS.append(A2)
    allok = True
    for i in range(10):
        ok, _ = await try_call(A2, "attachment_put", room="pack-main", filename=f"t{i}.png",
                               mime="image/png", data_base64=b64(png_bytes(64 + i)))
        allok = allok and ok
    note("attach: 10 uploads/day ok", allok)
    ok, err = await try_call(A2, "attachment_put", room="pack-main", filename="t11.png",
                             mime="image/png", data_base64=b64(png_bytes(200)))
    note("attach: 11th upload in a day rejected", not ok and "quota" in err, es(err))
    note("attach: quota rejections audited",
         audit_n("external_attachment_quota", "ext-att2") >= 1)
    room_total = dbq("SELECT COALESCE(SUM(size),0) AS s FROM attachments WHERE room_id=?",
                     (main_room_id,))[0]["s"]
    note("attach: room quota accounting intact (<500MB)", room_total < 500 * 1024 * 1024)

    # ---------- 9. egress_check 频控 ----------
    _, eg = await make_external("ext-egress")
    EG = eg["token"]; SECRETS.append(EG)
    allok = True
    for i in range(60):
        ok, r = await try_call(EG, "egress_check", url="https://example.com/page")
        allok = allok and ok and r.get("decision") == "ask"
    note("egress: 60 checks/min ok", allok)
    ok, err = await try_call(EG, "egress_check", url="https://example.com/page")
    note("egress: 61st check within a minute throttled", not ok and "rate_limited" in err)
    note("egress: throttle audited", audit_n("external_egress_throttled", "ext-egress") >= 1)
    ok, _ = await try_call(ALICE, "egress_check", url="https://example.com/page")
    note("egress: internal member not throttled by external cap", ok)

    # ---------- 10. 自动接力排除 external ----------
    reg = await call(DISP, "member_register", name="carol")
    CAROL = reg["token"]; SECRETS.append(CAROL)
    await call(CAROL, "heartbeat")
    await call(DISP, "member_quota_set", member_name="carol", week_used_pct=50)
    _, er = await make_external("ext-relay")
    ER = er["token"]; SECRETS.append(ER)
    await call(ER, "heartbeat")
    await call(DISP, "member_quota_set", member_name="ext-relay", week_used_pct=5)
    t_rb = (await call(DISP, "task_create", room="pack-main", title="relay source",
                       body="b", assignee_name="bob"))["task_id"]
    cl = await call(BOB, "task_claim", task_id=t_rb)
    blk = await call(BOB, "task_block", task_id=t_rb, claim_token=cl["claim_token"],
                     reason="额度没了", fail_class="quota")
    note("relay: quota-block relayed to internal candidate, not external",
         blk.get("relayed_to") == "carol", f"relayed_to={blk.get('relayed_to')}")
    tl = await call(DISP, "task_list", assignee_name="carol")
    note("relay: task now pending-acceptance under carol",
         any(t["id"] == t_rb for t in tl["tasks"]))

    # ---------- 11. 配对码边界：过期 / 吊销 / 锁定 / reclaim ----------
    pc = await call(DISP, "pair_code_create", member_name="ext-exp", provider="codex",
                    room="pack-main")
    SECRETS.append(pc["pair_code"])
    dbx("UPDATE pair_codes SET expires_at=? WHERE id=?", (time.time() - 10, pc["code_id"]))
    st, resp = pair_post({"code": pc["pair_code"]})
    note("pair: expired code rejected", st == 400)
    lst = await call(DISP, "pair_code_list")
    row = [x for x in lst["pair_codes"] if x["code_id"] == pc["code_id"]][0]
    note("pair: list shows expired", row["status"] == "expired")
    pc = await call(DISP, "pair_code_create", member_name="ext-rev", provider="codex",
                    room="pack-main")
    SECRETS.append(pc["pair_code"])
    await call(DISP, "pair_code_revoke", code_id=pc["code_id"])
    st, resp = pair_post({"code": pc["pair_code"]})
    note("pair: revoked code rejected", st == 400)
    ok, err = await try_call(DISP, "pair_code_revoke", code_id=pc["code_id"])
    note("pair: double revoke rejected", not ok)
    ok, err = await try_call(DISP, "pair_code_revoke", code_id=99999)
    note("pair: revoke nonexistent rejected", not ok)

    pcA = await call(DISP, "pair_code_create", member_name="ext-lock", provider="codex",
                     room="pack-main")
    pcB = await call(DISP, "pair_code_create", member_name="ext-lock", provider="codex",
                     room="pack-main")
    SECRETS.extend([pcA["pair_code"], pcB["pair_code"]])
    st, resp = pair_post({"code": pcB["pair_code"]})
    note("pair: second code redeems first (member created)", st == 200)
    LK_TOK = resp["token"]; SECRETS.append(LK_TOK)
    for i in range(5):
        st, resp = pair_post({"code": pcA["pair_code"]})
        if st != 400:
            note(f"pair: blocked-name redeem attempt {i+1} should be 400", False, f"st={st}")
    lst = await call(DISP, "pair_code_list")
    rowA = [x for x in lst["pair_codes"] if x["code_id"] == pcA["code_id"]][0]
    note("pair: code locked after 5 failed attempts",
         rowA["status"] == "locked" and rowA["fail_count"] == 5,
         f"status={rowA['status']} fails={rowA['fail_count']}")
    note("pair: lock audited at high priority", audit_n("pair_code_locked") >= 1)
    st, resp = pair_post({"code": pcA["pair_code"]})
    note("pair: locked code stays rejected", st == 400)

    # reclaim：只换 token，水位与游标不动
    _, rc = await make_external("ext-re", provider="codex")
    RC_TOK1 = rc["token"]; RC_ID = rc["member_id"]
    j0 = dbq("SELECT joined_msg_id FROM members WHERE id=?", (RC_ID,))[0]["joined_msg_id"]
    dbx("UPDATE member_read_cursors SET cursor_id=? WHERE member_id=?", (j0 + 5, RC_ID))
    pc = await call(DISP, "pair_code_create", member_name="ext-re", provider="gemini",
                    room="pack-main", reclaim=True)
    SECRETS.append(pc["pair_code"])
    st, resp = pair_post({"code": pc["pair_code"]})
    note("reclaim: redeem ok", st == 200 and resp["member_id"] == RC_ID)
    RC_TOK2 = resp["token"]; SECRETS.append(RC_TOK2)
    ok, _ = await try_call(RC_TOK1, "heartbeat")
    note("reclaim: old token dead", not ok)
    ok, _ = await try_call(RC_TOK2, "heartbeat")
    note("reclaim: new token works", ok)
    mrow = dbq("SELECT joined_msg_id, provider FROM members WHERE id=?", (RC_ID,))[0]
    cur = dbq("SELECT cursor_id FROM member_read_cursors WHERE member_id=?", (RC_ID,))[0]
    note("reclaim: watermark + cursor untouched, provider refreshed",
         mrow["joined_msg_id"] == j0 and cur["cursor_id"] == j0 + 5
         and mrow["provider"] == "gemini")
    ok, err = await try_call(DISP, "pair_code_create", member_name="ext-re",
                             provider="codex", room="pack-main")
    note("reclaim: plain code for active external rejected at create", not ok)

    # ---------- 12. member_token_rotate / member_rename ----------
    rot = await call(DISP, "member_token_rotate", name="codex-a")
    SECRETS.append(rot["token"])
    ok, _ = await try_call(EXT_A, "heartbeat")
    note("rotate: old token dead", not ok)
    EXT_A = rot["token"]
    ok, _ = await try_call(EXT_A, "heartbeat")
    note("rotate: new token works", ok)
    ok, err = await try_call(DISP, "member_token_rotate", name="nobody")
    note("rotate: nonexistent member rejected", not ok)
    rn = await call(DISP, "member_rename", name="codex-a", display_name="群主的 Codex 二号")
    note("rename: returns new display_name", rn["display_name"] == "群主的 Codex 二号")
    ml_d = await call(DISP, "member_list")
    de = [m for m in ml_d["members"] if m["name"] == "codex-a"][0]
    note("rename: only display_name changed", de["display_name"] == "群主的 Codex 二号")
    ok, err = await try_call(ALICE, "member_rename", name="bob", display_name="x")
    note("rename: member cannot rename (dispatcher-only)", not ok)

    # ---------- 13. external_gate 总开关 ----------
    pc = await call(DISP, "pair_code_create", member_name="ext-gate", provider="codex",
                    room="pack-main")
    SECRETS.append(pc["pair_code"])
    g = await call(DISP, "external_gate", enabled=False, reason="test")
    note("gate: set off", g["external_enabled"] is False)
    g = await call(DISP, "external_gate")
    note("gate: query shows off", g["external_enabled"] is False)
    ok, _ = await try_call(EXT_A, "heartbeat")
    note("gate off: external token rejected everywhere", not ok)
    st, resp = pair_post({"code": pc["pair_code"]})
    note("gate off: /pair closed (403)", st == 403, f"st={st}")
    ok, err = await try_call(DISP, "pair_code_create", member_name="ext-gate2",
                             provider="codex", room="pack-main")
    note("gate off: pair_code_create blocked", not ok)
    ok, _ = await try_call(ALICE, "heartbeat")
    note("gate off: internal members unaffected", ok)
    ok, _ = await try_call(DISP, "member_list")
    note("gate off: dispatcher unaffected", ok)
    g = await call(DISP, "external_gate", enabled=True)
    note("gate: set back on", g["external_enabled"] is True)
    ok, _ = await try_call(EXT_A, "heartbeat")
    note("gate on: external token works again", ok)
    st, resp = pair_post({"code": pc["pair_code"]})
    note("gate on: /pair works again", st == 200)
    if st == 200:
        SECRETS.append(resp["token"])

    # ---------- 14. 移除成员：token 立即失效、名字保留 ----------
    reg = await call(DISP, "member_register", name="temp-x")
    TX = reg["token"]; SECRETS.append(TX)
    await call(DISP, "member_remove", name="temp-x")
    ok, _ = await try_call(TX, "heartbeat")
    note("remove: internal token dead immediately", not ok)
    ok, err = await try_call(DISP, "pair_code_create", member_name="temp-x",
                             provider="codex", room="pack-main")
    note("remove: pair create for removed name reserved-rejected",
         not ok and "reserved" in err, es(err))
    _, gn = await make_external("ext-gone")
    GN = gn["token"]
    await call(DISP, "member_remove", name="ext-gone")
    ok, _ = await try_call(GN, "heartbeat")
    note("remove: external token dead immediately", not ok)
    ok, err = await try_call(DISP, "member_token_rotate", name="ext-gone")
    note("remove: rotate removed member rejected", not ok)
    ok, err = await try_call(DISP, "member_rename", name="ext-gone", display_name="x")
    note("remove: rename removed member rejected", not ok)

    # ---------- 15. 内部成员关键行为回归 ----------
    t_reg = (await call(DISP, "task_create", room="pack-main", title="alice full flow",
                        body="b", assignee_name="alice"))["task_id"]
    cl = await call(ALICE, "task_claim", task_id=t_reg)
    await call(ALICE, "run_start", task_id=t_reg, claim_token=cl["claim_token"])
    await call(ALICE, "task_update", task_id=t_reg, claim_token=cl["claim_token"], note="干活中")
    comp = await call(ALICE, "task_complete", task_id=t_reg,
                      claim_token=cl["claim_token"], result="",
                      envelope={"verdict": "done", "summary": "全链交活",
                                "artifacts": [{"kind": "file", "path": "/tmp/x"}]})
    note("regression: internal envelope complete ok", comp["status"] == "review")
    await call(DISP, "task_review", task_id=t_reg, approve=True)
    qr = await call(ALICE, "member_quota_report", week_used_pct=42)
    note("regression: quota self-report ok", qr["quota_week_used_pct"] == 42.0
         and qr["quota_source"] == "self")
    ok, err = await try_call(ALICE, "memory_read", scope="private", member="bob")
    note("regression: reading other's memory forbidden", not ok)
    mw = await call(ALICE, "memory_write", scope="private", key="note1", text="自己的记忆")
    mr = await call(ALICE, "memory_read", scope="private", key="note1")
    note("regression: own memory write/read ok", mr["text"] == "自己的记忆")
    rd = await call(ALICE, "room_read_new", room="pack-main", since_id=0, limit=5, tail=True)
    allids = dbq("SELECT id FROM messages WHERE room_id=? ORDER BY id DESC LIMIT 5",
                 (main_room_id,))
    note("regression: tail read returns latest ascending",
         [x["id"] for x in rd["messages"]] == sorted(r[0] for r in allids))
    tl = await call(ALICE, "task_list", status="open,claimed")
    note("regression: multi-status task_list ok",
         all(t["status"] in ("open", "claimed") for t in tl["tasks"]))
    eg = await call(ALICE, "egress_check", url="https://example.com/x")
    note("regression: egress_check ok", eg["decision"] in ("ask", "allow"))

    # ---------- 16. 秘密卫生：码/token 不进审计、日志、房间 ----------
    audit_dump = json.dumps([dict(r) for r in dbq("SELECT actor, action, detail FROM audit")],
                            ensure_ascii=False)
    msg_dump = json.dumps([r[0] for r in dbq("SELECT body FROM messages")], ensure_ascii=False)
    log_txt = ""
    for fn in ("server.log",):
        p = os.path.join(HERE, fn)
        if os.path.exists(p):
            log_txt += open(p, errors="ignore").read()
    leaked = [s for s in SECRETS if s and (s in audit_dump or s in msg_dump or s in log_txt)]
    note("secrets: no pair code / member token in audit, messages or server log",
         not leaked, f"leaked={len(leaked)}")
    hashes = dbq("SELECT code_hash FROM pair_codes")
    note("secrets: pair codes stored as sha256 only",
         all(len(h["code_hash"]) == 64 and h["code_hash"] not in SECRETS for h in hashes))
    th = dbq("SELECT token_hash FROM members")
    note("secrets: member tokens stored as sha256 only",
         all(t["token_hash"] not in SECRETS for t in th))

    with open(os.path.join(HERE, "test_fixtures.json"), "w") as f:
        json.dump({"owner": OWNER, "codex_a": EXT_A, "alice": ALICE}, f)
    print(f"== {sum(res)}/{len(res)} PASS", flush=True)
    if not all(res):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
