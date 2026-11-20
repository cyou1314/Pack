# verify_item5.py — EXTERNAL-AGENT 验收项 5 外部视角对抗验证（调度员按 Gemini 规格实现）
# 裸 JSON-RPC over HTTP（无 SDK），只用 Python 标准库。打隔离 staging 实例 attack51。
import base64, json, os, sys, urllib.request, urllib.error

BASE = os.environ.get("HUB_BASE", "http://127.0.0.1:18799").rstrip("/")
MCP = BASE + "/mcp"; PAIR = BASE + "/pair"
DISP = os.environ.get("DISP_TOKEN") or open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dispatcher_token")).read().strip()
PAIR_CODE = os.environ["PAIR_CODE"]
RESULTS = []
_id = [0]

def rec(cat, name, ok, detail=""):
    RESULTS.append((cat, name, bool(ok)))
    print(f"[{cat}] {'PASS' if ok else 'FAIL'} | {name} | {str(detail)[:180]}")

_sessions = {}

def _post(token, body, session=None):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}",
               "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(MCP, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode(), r.headers.get("Mcp-Session-Id")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers.get("Mcp-Session-Id") if e.headers else None
    except urllib.error.URLError as e:
        return 0, f"URLError: {e}", None

def _ensure_session(token):
    if token in _sessions:
        return _sessions[token]
    _id[0] += 1
    init = {"jsonrpc": "2.0", "id": _id[0], "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "verify-item5", "version": "1.0"}}}
    st, txt, sid = _post(token, json.dumps(init).encode())
    if st != 200 or not sid:
        raise RuntimeError(f"initialize failed: HTTP {st} {txt[:150]}")
    _post(token, json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(), sid)
    _sessions[token] = sid
    return sid

def rpc(token, method, params=None, raw_body=None):
    sid = _ensure_session(token)
    _id[0] += 1
    body = raw_body if raw_body is not None else json.dumps(
        {"jsonrpc": "2.0", "id": _id[0], "method": method, "params": params or {}}).encode()
    st, txt, _ = _post(token, body, sid)
    return st, txt

def call(token, tool, **kw):
    """tools/call；返回 (ok, parsed_result_or_error_text)"""
    st, txt = rpc(token, "tools/call", {"name": tool, "arguments": kw})
    if st != 200:
        return False, f"HTTP {st}: {txt[:150]}"
    # SSE or plain JSON
    payload = None
    for line in txt.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
    if payload is None:
        payload = txt
    try:
        d = json.loads(payload)
    except Exception:
        return False, f"unparsable: {txt[:150]}"
    res = d.get("result")
    if res is None:
        return False, f"no result: {str(d)[:150]}"
    if res.get("isError"):
        return False, res["content"][0]["text"] if res.get("content") else "tool error"
    t = res["content"][0]["text"] if res.get("content") else ""
    try:
        return True, json.loads(t)
    except Exception:
        return True, t

def pair_post(payload=None, raw=None):
    data = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(PAIR, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        b = e.read().decode()
        try: return e.code, json.loads(b or "{}")
        except Exception: return e.code, {"raw": b[:150]}

PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")).decode()

def main():
    # ---------- 场景布置（调度员） ----------
    ok, r = call(DISP, "room_post", room="pack-main", body="入群前历史消息 A", kind="chat")
    assert ok, r
    ok, r = call(DISP, "room_post", room="pack-main", body="入群前历史消息 B", kind="chat")
    assert ok, r
    hist_b_id = r["message_id"]
    ok, r = call(DISP, "room_post", room="side-room", body="对比房消息", kind="chat")
    assert ok, r
    ok, r = call(DISP, "attachment_put", room="pack-main", filename="pre.png", mime="image/png", data_base64=PNG)
    assert ok, r
    pre_att = r["attachment_id"]

    # ---------- A 配对码边界 ----------
    st, r = pair_post({"code": "pk-pair-WRONGWRONGWRONG"})
    rec("pair", "错误配对码拒绝且不泄信息", st == 400 and "token" not in json.dumps(r), f"status={st} body={str(r)[:100]}")
    st, r = pair_post(raw=b"{not json")
    rec("pair", "畸形 JSON 拒绝", st == 400, f"status={st}")
    st, r = pair_post({"code": PAIR_CODE})
    ext_ok = st == 200 and "token" in r
    rec("pair", "正确配对码兑换成功", ext_ok, f"status={st} keys={list(r)[:6]}")
    if not ext_ok:
        print("FATAL: pair failed, abort"); sys.exit(1)
    EXT = r["token"]
    st, r2 = pair_post({"code": PAIR_CODE})
    rec("pair", "同一配对码二次兑换拒绝（一码一用）", st == 400, f"status={st}")
    ok, ml = call(EXT, "member_list")
    joined = None
    if ok:
        for m in ml["members"]:
            if m["name"] == "codex-ext":
                joined = m
    rec("pair", "兑换后成员 kind=external 且绑定 pack-main",
        bool(joined) and joined.get("kind") == "external", str(joined)[:150])

    # ---------- B 入群水位 ----------
    ok, rr = call(EXT, "room_read_new", room="pack-main", since_id=0)
    bodies = [m["body"] for m in rr["messages"]] if ok else []
    rec("watermark", "external 读绑定房看不到入群前历史",
        ok and not any("入群前" in b for b in bodies), f"n={len(bodies)}")
    ok2, e2 = call(EXT, "room_read_new", room="side-room", since_id=0)
    rec("watermark", "external 读对比房被拒", not ok2, str(e2)[:120])
    ok3, e3 = call(EXT, "attachment_get", attachment_id=pre_att)
    rec("watermark", "external 取入群前附件被拒", not ok3, str(e3)[:120])
    ok4, al = call(EXT, "attachment_list", room="pack-main")
    ids = [a["id"] for a in al["attachments"]] if ok4 else [-1]
    rec("watermark", "external 附件列表不含入群前附件", ok4 and pre_att not in ids, f"ids={ids}")
    ok5, rl = call(EXT, "room_list")
    names = [x["name"] for x in rl["rooms"]] if ok5 else []
    rec("watermark", "external 房间列表只见绑定房", ok5 and names == ["pack-main"], f"rooms={names}")
    ok, r = call(DISP, "room_post", room="pack-main", body="入群后新消息", kind="chat")
    assert ok, r
    ok6, rr2 = call(EXT, "room_read_new", room="pack-main", since_id=0)
    bodies2 = [m["body"] for m in rr2["messages"]] if ok6 else []
    rec("watermark", "external 能看到入群后消息", ok6 and any("入群后" in b for b in bodies2), f"n={len(bodies2)}")

    # ---------- C 白名单闸门 ----------
    denied = ["task_create", "task_transfer", "task_accept_transfer", "task_schedule",
              "task_review", "task_cancel", "member_register", "member_remove",
              "pair_code_create", "external_gate", "room_create", "room_archive",
              "memory_read", "memory_write", "skill_list", "skill_publish",
              "desktop_status", "desktop_acquire", "rule_list", "template_upsert",
              "member_quota_set", "member_token_rotate", "room_summary_set"]
    all_denied = True; details = []
    for t in denied:
        okx, ex = call(EXT, t)
        if okx:
            all_denied = False; details.append(f"{t} ALLOWED!")
    rec("whitelist", f"白名单外 {len(denied)} 个工具全拒", all_denied, ";".join(details) or "all denied")
    ok7, ml2 = call(EXT, "member_list")
    keys = set()
    if ok7:
        for m in ml2["members"]:
            keys |= set(m.keys())
    rec("whitelist", "member_list 脱敏视图无敏感键",
        ok7 and not ({"token_hash", "bound_room_id", "joined_msg_id"} & keys), f"keys={sorted(keys)}")
    ok8, hb = call(EXT, "heartbeat")
    rec("whitelist", "白名单内 heartbeat 可用", ok8, str(hb)[:80])

    # ---------- D 老接口越权 ----------
    ok9, e9 = call(EXT, "room_post", room="side-room", body="越房发言", kind="chat")
    rec("legacy", "external 向对比房发帖被拒", not ok9, str(e9)[:120])
    ok10, e10 = call(EXT, "room_post", room="pack-main", body="伪造任务卡", kind="task_card")
    rec("legacy", "external 伪造 task_card 被拒", not ok10, str(e10)[:120])
    ok11, e11 = call(EXT, "room_post", room="pack-main", body="批准 任务#1", kind="decision")
    rec("legacy", "external 伪造 decision 被拒", not ok11, str(e11)[:120])
    # 任务可见性与认领
    ok, t1 = call(DISP, "task_create", room="pack-main", title="给外部的任务", body="请处理", assignee_name="codex-ext")
    assert ok, t1; own_task = t1["task_id"]
    ok, t2 = call(DISP, "task_create", room="pack-main", title="未指派任务", body="无主")
    assert ok, t2; free_task = t2["task_id"]
    ok, tl = call(EXT, "task_list")
    tids = [t["id"] for t in tl["tasks"]] if ok else []
    rec("legacy", "task_list 只回指派本人的任务", ok and own_task in tids and free_task not in tids, f"ids={tids}")
    ok12, e12 = call(EXT, "task_claim", task_id=free_task)
    rec("legacy", "external 认领未指派任务被拒", not ok12, str(e12)[:120])
    ok13, cl = call(EXT, "task_claim", task_id=own_task)
    rec("legacy", "external 认领指派本人的任务成功", ok13 and "claim_token" in str(cl), str(cl)[:100])
    ctok = cl["claim_token"] if ok13 else ""
    # 转交反向（修复 1 的实打）
    ok, t3 = call(DISP, "task_create", room="pack-main", title="调度员自持任务", body="转交测试")
    assert ok, t3
    ok14, cl2 = call(DISP, "task_claim", task_id=t3["task_id"])
    dtok = cl2["claim_token"] if ok14 else ""
    ok15, e15 = call(DISP, "task_transfer", task_id=t3["task_id"], claim_token=dtok, to_member="codex-ext")
    rec("legacy", "调度员把任务转给 external 被拒（红队修复1）", not ok15 and "external" in str(e15), str(e15)[:140])
    ok16, e16 = call(EXT, "message_edit", message_id=hist_b_id, new_body="篡改历史")
    rec("legacy", "external 编辑他人消息被拒", not ok16, str(e16)[:120])

    # ---------- E 配额与频控 ----------
    ok17, a1 = call(EXT, "attachment_put", room="pack-main", filename="ok.png", mime="image/png", data_base64=PNG)
    rec("quota", "external 小附件上传可用", ok17, str(a1)[:100])
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024)).decode()
    ok18, e18 = call(EXT, "attachment_put", room="pack-main", filename="big.png", mime="image/png", data_base64=big)
    rec("quota", "external 5MB+ 附件未被接受（工具拒或传输层413）", not ok18, str(e18)[:130])
    # 发帖频控 5/分：前面 external 已发 0 条成功帖（越权帖均被拒不计），连发 6 条
    sent = 0; limited = False
    for i in range(7):
        okx, ex = call(EXT, "room_post", room="pack-main", body=f"频控测试 {i}", kind="chat")
        if okx: sent += 1
        elif "retry_after" in str(ex) or "rate" in str(ex) or "limit" in str(ex) or "频繁" in str(ex) or "稍后" in str(ex): limited = True
    rec("quota", "发帖频控 5/分生效", sent <= 5 and limited, f"sent={sent} limited={limited}")
    # egress 频控 60/分
    throttled = False
    for i in range(65):
        okx, ex = call(EXT, "egress_check", url="example.com")
        if not okx and ("60" in str(ex) or "rate" in str(ex) or "limit" in str(ex) or "频繁" in str(ex)):
            throttled = True; break
    rec("quota", "egress_check 60/分频控生效", throttled, f"i={i}")

    # ---------- F external_gate 总开关 ----------
    ok, g1 = call(DISP, "external_gate", enabled=False)
    assert ok, g1
    ok19, e19 = call(EXT, "member_list")
    rec("gate", "总开关关闭后 external 鉴权被拒", not ok19, str(e19)[:120])
    ok, code2 = call(DISP, "pair_code_create", member_name="codex-two", provider="codex", room="pack-main")
    gate_code_blocked = not ok
    if ok:
        st, r3 = pair_post({"code": code2["pair_code"]})
        gate_code_blocked = st == 400
    rec("gate", "总开关关闭后新配对不可兑换", gate_code_blocked, "")
    ok, g2 = call(DISP, "external_gate", enabled=True)
    assert ok, g2
    ok20, _ = call(EXT, "heartbeat")
    rec("gate", "总开关重开后 external 恢复", ok20, "")

    fails = [r for r in RESULTS if not r[2]]
    print(f"== TOTAL {len(RESULTS)} PASS {len(RESULTS)-len(fails)} FAIL {len(fails)}")
    for c, n, _ in fails:
        print(f"FAILED: [{c}] {n}")
    sys.exit(1 if fails else 0)

main()
