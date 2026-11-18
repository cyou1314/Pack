import asyncio, base64, json, os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, call_err, DISP
PNG = base64.b64encode(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")).decode()
res = []
def note(name, ok, extra=""):
    res.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)

async def mk_task(tok_owner, title):
    t = await call(DISP, "task_create", room="pack-main", title=title, body="b", assignee_name="alice")
    cl = await call(tok_owner, "task_claim", task_id=t["task_id"])
    return t["task_id"], cl["claim_token"]

async def main():
    alice = (await call(DISP, "member_register", name="alice"))["token"]
    bob = (await call(DISP, "member_register", name="bob"))["token"]
    await call(DISP, "room_create", name="other-room")
    a1 = (await call(alice, "attachment_put", room="pack-main", filename="a.png", mime="image/png", data_base64=PNG))["attachment_id"]
    a2 = (await call(alice, "attachment_put", room="pack-main", filename="b.png", mime="image/png", data_base64=PNG))["attachment_id"]
    tid, tok = await mk_task(alice, "T1 happy")
    out = await call(alice, "task_complete", task_id=tid, claim_token=tok, result="done", attachment_ids=json.dumps([a1, a2]))
    note("1 happy completes", out["status"] == "review" and out.get("attachments") == 2)
    msgs = (await call(DISP, "room_read_new", room="pack-main", since_id=0, limit=200))["messages"]
    rep = [m for m in msgs if m.get("task_id") == tid and m["kind"] == "report"]
    note("1 report carries 2 attachments", len(rep) == 1 and len(rep[0].get("attachments", [])) == 2)
    au = await call(DISP, "task_audit", task_id=tid)
    note("10 audit has attachments=2", any("attachments=2" in e["detail"] for e in au["events"] if e["action"] == "task_complete"))
    b1 = (await call(bob, "attachment_put", room="pack-main", filename="c.png", mime="image/png", data_base64=PNG))["attachment_id"]
    tid2, tok2 = await mk_task(alice, "T2 other")
    e = await call_err(alice, "task_complete", task_id=tid2, claim_token=tok2, result="x", attachment_ids=json.dumps([b1]))
    tl = await call(DISP, "task_list", assignee_name="alice")
    st = {t["id"]: t["status"] for t in tl["tasks"]}
    note("2 other's attachment rejected + still working", e is not None and st[tid2] in ("claimed", "working"), str(e)[:60])
    e = await call_err(alice, "task_complete", task_id=tid2, claim_token=tok2, result="x", attachment_ids=json.dumps([a1]))
    note("3 already-bound rejected", e is not None)
    a3 = (await call(alice, "attachment_put", room="other-room", filename="d.png", mime="image/png", data_base64=PNG))["attachment_id"]
    e = await call_err(alice, "task_complete", task_id=tid2, claim_token=tok2, result="x", attachment_ids=json.dumps([a3]))
    note("4 cross-room rejected", e is not None)
    e = await call_err(alice, "task_complete", task_id=tid2, claim_token=tok2, result="x", attachment_ids=json.dumps([1, 2, 3, 4, 5]))
    note("5 >4 rejected", e is not None)
    e = await call_err(alice, "task_complete", task_id=tid2, claim_token=tok2, result="x", attachment_ids="not-json")
    note("6 invalid JSON rejected", e is not None)
    a4 = (await call(alice, "attachment_put", room="pack-main", filename="e.png", mime="image/png", data_base64=PNG))["attachment_id"]
    e = await call_err(alice, "task_complete", task_id=tid2, claim_token=tok2, result="x", attachment_ids=json.dumps([a4, 99999]))
    conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pack.db"))
    row = conn.execute("SELECT message_id FROM attachments WHERE id=?", (a4,)).fetchone()
    nrep = conn.execute("SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='report'", (tid2,)).fetchone()[0]
    conn.close()
    tl = await call(DISP, "task_list", assignee_name="alice")
    st = {t["id"]: t["status"] for t in tl["tasks"]}
    note("7 mixed rollback (working, no report, good att unbound)",
         e is not None and st[tid2] in ("claimed", "working") and row[0] is None and nrep == 0)
    out = await call(alice, "task_complete", task_id=tid2, claim_token=tok2, result="plain")
    note("8 legacy no-param complete unchanged", out["status"] == "review" and "attachments" not in out)
    a5 = (await call(alice, "attachment_put", room="pack-main", filename="f.png", mime="image/png", data_base64=PNG))["attachment_id"]
    tid3, tok3 = await mk_task(alice, "T3 envelope")
    out = await call(alice, "task_complete", task_id=tid3, claim_token=tok3, result="ignored",
                     envelope={"verdict": "done", "summary": "env summary"}, attachment_ids=json.dumps([a5]))
    note("9 envelope+attachments", out["status"] == "review" and out.get("verdict") == "done" and out.get("attachments") == 1)
    print("RESULT:", "ALL PASS" if all(res) else f"{res.count(False)} FAILURES", flush=True)
    sys.exit(0 if all(res) else 1)
asyncio.run(main())
