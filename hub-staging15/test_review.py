#!/usr/bin/env python3
"""S1 slow-review dynamic checks (isolated: port 8813 + fresh DB in review-iso).
Covers review items 1 (auth), 2 (concurrency), 3 (limits), 4 (integrity),
5 (audit), 6 (error-path robustness). Prints PASS/FAIL plus evidence lines."""
import asyncio
import json
import sqlite3

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "http://127.0.0.1:8813/mcp"
BASE = "/home/hatch/workspace/pack/hub-staging15"
MARK = "UNIQUE-MARKER-7f3a"

passed = 0
failed = 0
findings = []


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")
        findings.append(f"{name} :: {extra}")


def note(text):
    print(f"  EVIDENCE {text}")


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


def sql(q, args=()):
    conn = sqlite3.connect(f"{BASE}/pack.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(q, args).fetchall()
    conn.close()
    return rows


def sq1(q, args=()):
    rows = sql(q, args)
    return rows[0] if rows else None


async def main():
    disp = open(f"{BASE}/.dispatcher_token").read().strip()
    absent = (await call(disp, "member_register", name="absent"))["token"]
    bert = (await call(disp, "member_register", name="bert"))["token"]
    owner = (await call(disp, "member_register", name="owner"))["token"]

    print("== A. auth boundaries ==")
    await call(disp, "skill_publish", name="auth-skill", body=f"{MARK} body v1",
               description="auth test")
    check("A1 owner list empty before assign",
          (await call(owner, "skill_list"))["skills"] == [])
    e = await call_err(owner, "skill_read", name="auth-skill")
    check("A2 owner read unassigned rejected", "not assigned" in e, e[:160])
    e = await call_err(owner, "skill_publish", name="o2", body="x")
    check("A3 owner publish rejected (dispatcher only)", "forbidden" in e, e[:160])
    e = await call_err(owner, "skill_assign", name="auth-skill", member_name="absent")
    check("A4 owner assign rejected", "forbidden" in e, e[:160])
    e = await call_err(owner, "skill_rollback", name="auth-skill", version=1)
    check("A5 owner rollback rejected", "forbidden" in e, e[:160])
    e = await call_err(bert, "skill_assign", name="auth-skill", member_name="bert")
    check("A6 member assign rejected", "forbidden" in e, e[:160])
    e = await call_err(bert, "skill_unassign", name="auth-skill", member_name="absent")
    check("A7 member unassign rejected", "forbidden" in e, e[:160])
    e = await call_err(bert, "skill_rollback", name="auth-skill", version=1)
    check("A8 member rollback rejected", "forbidden" in e, e[:160])
    e = await call_err("pk-mem-bogus-token", "skill_list")
    check("A10 bad token rejected", "unauthorized" in e, e[:160])
    e = await call_err(absent, "skill_read", name="ghost-skill")
    check("A11 member read nonexistent -> unified forbidden (no existence leak)",
          "not assigned" in e and "no such skill" not in e, e[:160])
    e = await call_err(disp, "skill_read", name="ghost-skill")
    check("A12 dispatcher read nonexistent -> no such skill", "no such skill" in e, e[:160])
    await call(disp, "skill_assign", name="auth-skill", member_name="owner")
    rd = await call(owner, "skill_read", name="auth-skill")
    check("A13 owner reads after assign", rd["body"] == f"{MARK} body v1", str(rd)[:120])
    await call(disp, "skill_unassign", name="auth-skill", member_name="owner")
    e = await call_err(owner, "skill_read", name="auth-skill")
    check("A14 owner read after unassign (enabled=0) rejected", "not assigned" in e, e[:160])

    print("== B. concurrency / atomicity ==")
    await call(disp, "skill_publish", name="race-skill", body="race base v1")
    res = await asyncio.gather(*[
        call(disp, "skill_publish", name="race-skill", body=f"race body {i}")
        for i in range(8)], return_exceptions=True)
    oks = [r for r in res if not isinstance(r, Exception)]
    errs = [str(r) for r in res if isinstance(r, Exception)]
    vers = sorted(x["version"] for x in sql(
        "SELECT version FROM skill_versions sv JOIN skills s ON s.id=sv.skill_id "
        "WHERE s.name='race-skill'"))
    cur = sq1("SELECT current_version FROM skills WHERE name='race-skill'")["current_version"]
    note(f"B1 8x concurrent publish existing: ok={len(oks)} err={len(errs)} "
         f"versions={vers} current_version={cur}")
    for e0 in errs[:3]:
        note(f"B1 err sample: {e0[:200]}")
    check("B1 all 8 concurrent publishes succeed", len(oks) == 8, f"ok={len(oks)}")
    check("B1 versions contiguous 1..9, no dup/gap",
          vers == list(range(1, 10)) and cur == 9, f"vers={vers} cur={cur}")
    res = await asyncio.gather(*[
        call(disp, "skill_publish", name="race-new", body=f"new body {i}")
        for i in range(6)], return_exceptions=True)
    oks = [r for r in res if not isinstance(r, Exception)]
    errs = [str(r) for r in res if isinstance(r, Exception)]
    nrow = sq1("SELECT COUNT(*) AS c FROM skills WHERE name='race-new'")["c"]
    vers = sorted(x["version"] for x in sql(
        "SELECT version FROM skill_versions sv JOIN skills s ON s.id=sv.skill_id "
        "WHERE s.name='race-new'"))
    note(f"B2 6x concurrent publish new name: ok={len(oks)} err={len(errs)} "
         f"skills_rows={nrow} versions={vers}")
    for e0 in errs[:3]:
        note(f"B2 err sample: {e0[:200]}")
    check("B2 exactly one skills row for raced new name", nrow == 1, f"rows={nrow}")
    check("B2 all 6 succeed with contiguous versions",
          len(oks) == 6 and vers == list(range(1, 7)), f"ok={len(oks)} vers={vers}")
    base_cur = sq1("SELECT current_version FROM skills WHERE name='race-skill'")["current_version"]
    res = await asyncio.gather(*[
        call(disp, "skill_rollback", name="race-skill", version=1)
        for _ in range(4)], return_exceptions=True)
    oks = [r for r in res if not isinstance(r, Exception)]
    errs = [str(r) for r in res if isinstance(r, Exception)]
    vers = sorted(x["version"] for x in sql(
        "SELECT version FROM skill_versions sv JOIN skills s ON s.id=sv.skill_id "
        "WHERE s.name='race-skill'"))
    cur = sq1("SELECT current_version FROM skills WHERE name='race-skill'")["current_version"]
    note(f"B3 4x concurrent rollback: ok={len(oks)} err={len(errs)} cur {base_cur}->{cur} "
         f"maxver={vers[-1] if vers else None} nvers={len(vers)}")
    for e0 in errs[:3]:
        note(f"B3 err sample: {e0[:200]}")
    check("B3 all 4 concurrent rollbacks succeed, versions unique+contiguous",
          len(oks) == 4 and cur == base_cur + 4 and len(vers) == len(set(vers)),
          f"ok={len(oks)} cur={cur}")

    print("== C. limit accounting ==")
    cjk = "规程"  # 2 CJK chars per unit
    body_ok = "规程" * 10000  # 20000 chars, 60000 utf-8 bytes
    r = await call(disp, "skill_publish", name="cjk-20000", body=body_ok)
    check("C1 body of 20000 CJK chars accepted (char counting)",
          r["version"] == 1, str(r))
    note(f"C1 len(body)={len(body_ok)} utf8_bytes={len(body_ok.encode('utf-8'))}")
    before = sq1("SELECT COUNT(*) AS c FROM skill_versions")["c"]
    e = await call_err(disp, "skill_publish", name="cjk-20000", body="规程" * 10000 + "x")
    after = sq1("SELECT COUNT(*) AS c FROM skill_versions")["c"]
    cur = sq1("SELECT current_version FROM skills WHERE name='cjk-20000'")["current_version"]
    check("C2 body of 20001 chars rejected", "skill_limit_exceeded" in e, e[:200])
    check("C2 rejection writes nothing (no version row, no bump)",
          before == after and cur == 1, f"rows {before}->{after} cur={cur}")
    r = await call(disp, "skill_publish", name="desc-200", body="b",
                   description="简" * 200)
    check("C3 description of 200 CJK chars accepted", r["version"] == 1, str(r))
    before = sq1("SELECT COUNT(*) AS c FROM skill_versions")["c"]
    e = await call_err(disp, "skill_publish", name="desc-200", body="new body",
                       description="简" * 201)
    after = sq1("SELECT COUNT(*) AS c FROM skill_versions")["c"]
    cur = sq1("SELECT current_version FROM skills WHERE name='desc-200'")["current_version"]
    check("C3 description of 201 chars rejected, no version written",
          "skill_desc_limit_exceeded" in e and before == after and cur == 1,
          f"{e[:120]} rows {before}->{after} cur={cur}")
    emoji_body = "\U0001F600" * 20000  # 20000 code points, astral plane
    r = await call(disp, "skill_publish", name="emoji-20000", body=emoji_body)
    check("C4 body of 20000 astral emoji accepted (code-point counting)",
          r["version"] == 1, str(r)[:120])
    note(f"C4 python len={len(emoji_body)} utf8_bytes={len(emoji_body.encode('utf-8'))} "
         f"utf16_units={len(emoji_body.encode('utf-16-le')) // 2}")

    print("== D. data integrity ==")
    e = await call_err(disp, "skill_rollback", name="auth-skill", version=0)
    check("D1 rollback version=0 cleanly rejected", "no such version" in e, e[:160])
    e = await call_err(disp, "skill_rollback", name="auth-skill", version=-3)
    check("D2 rollback negative version cleanly rejected", "no such version" in e, e[:160])
    e = await call_err(disp, "skill_rollback", name="ghost-skill", version=1)
    check("D3 rollback nonexistent skill cleanly rejected", "no such skill" in e, e[:160])
    n0 = sq1("SELECT COUNT(*) AS c FROM member_skills")["c"]
    e = await call_err(disp, "skill_assign", name="auth-skill", member_name="ghost")
    n1 = sq1("SELECT COUNT(*) AS c FROM member_skills")["c"]
    check("D4 assign to nonexistent member rejected, no row written",
          "no such member" in e and n0 == n1, f"{e[:120]} rows {n0}->{n1}")
    # unassign a member who was never assigned (bert never got auth-skill)
    n0 = sq1("SELECT COUNT(*) AS c FROM member_skills")["c"]
    r = await call(disp, "skill_unassign", name="auth-skill", member_name="bert")
    n1 = sq1("SELECT COUNT(*) AS c FROM member_skills")["c"]
    row = sq1("SELECT enabled FROM member_skills ms JOIN members m ON m.id=ms.member_id "
              "JOIN skills s ON s.id=ms.skill_id "
              "WHERE m.name='bert' AND s.name='auth-skill'")
    note(f"D5 unassign never-assigned: result={r} member_skills rows {n0}->{n1} "
         f"row={dict(row) if row else None}")
    check("D5 unassign of never-assigned creates no row",
          n0 == n1 and row is None, f"rows {n0}->{n1} row={dict(row) if row else None}")
    for nm, bd in [("a'b", "quote body"), ("100%_x", "percent body"),
                   ("a_b", "underscore body"), ("aXb", "axb body")]:
        await call(disp, "skill_publish", name=nm, body=bd)
    rd = await call(disp, "skill_read", name="100%_x")
    check("D6 read '100%_x' exact", rd["body"] == "percent body", str(rd)[:120])
    rd = await call(disp, "skill_read", name="a_b")
    check("D6 read 'a_b' exact (no LIKE bleed from aXb)", rd["body"] == "underscore body",
          str(rd)[:120])
    rd = await call(disp, "skill_read", name="a'b")
    check("D6 read name with quote exact", rd["body"] == "quote body", str(rd)[:120])
    e = await call_err(disp, "skill_read", name="100zzx")
    check("D6 read '100zzx' -> no such skill (no wildcard match)",
          "no such skill" in e, e[:160])
    e = await call_err(disp, "skill_read", name="a%b")
    check("D6 read 'a%b' -> no such skill", "no such skill" in e, e[:160])
    await call(disp, "skill_publish", name="  padded  ", body="pad v1")
    names = [x["name"] for x in (await call(disp, "skill_list"))["skills"]]
    check("D7 publish strips name ('padded' created)", "padded" in names, str(names))
    e = await call_err(disp, "skill_read", name="  padded  ")
    note(f"D7 read unstripped name result: {e[:140]}")
    check("D7 read with same unstripped name finds the skill",
          "no such skill" not in e, e[:140])
    r = await call(disp, "skill_publish", name="padded", body="pad v2")
    n = sq1("SELECT COUNT(*) AS c FROM skills WHERE name='padded'")["c"]
    check("D7 re-publish stripped name bumps same row", r["version"] == 2 and n == 1,
          f"{r} rows={n}")
    e = await call_err(disp, "skill_publish", name="   ", body="x")
    check("D8 whitespace-only name cleanly rejected", "must not be empty" in e, e[:160])
    e = await call_err(disp, "skill_publish", name="ok-name", body="")
    check("D9 empty body cleanly rejected", "non-empty" in e, e[:160])

    print("== E. audit completeness ==")
    rows = sql("SELECT actor, action, detail FROM audit WHERE action LIKE 'skill_%' "
               "ORDER BY id")
    allowed = {"skill", "version", "member", "restored_from", "new_version"}
    bad = []
    for x in rows:
        try:
            d = json.loads(x["detail"] or "{}")
        except Exception:
            bad.append(("unparsable", x["detail"]))
            continue
        if not set(d.keys()) <= allowed:
            bad.append(("keys", d))
        if MARK in (x["detail"] or "") or "race body" in (x["detail"] or ""):
            bad.append(("body-leak", x["detail"]))
        if x["actor"] != "dispatcher":
            bad.append(("actor", x["actor"]))
    acts = {x["action"] for x in rows}
    check("E1 audit rows metadata-only, actor=dispatcher",
          not bad and {"skill_publish", "skill_assign", "skill_unassign",
                       "skill_rollback"} <= acts,
          f"bad={bad[:3]} acts={acts}")
    n0 = sq1("SELECT COUNT(*) AS c FROM audit WHERE action LIKE 'skill_%'")["c"]
    await call_err(disp, "skill_publish", name="cjk-20000", body="z" * 20001)
    await call_err(absent, "skill_publish", name="zz", body="zz")
    await call_err(disp, "skill_rollback", name="auth-skill", version=777)
    n1 = sq1("SELECT COUNT(*) AS c FROM audit WHERE action LIKE 'skill_%'")["c"]
    check("E2 failed operations write no audit rows", n0 == n1, f"{n0}->{n1}")

    print("== F. error-path robustness ==")
    for i in range(60):
        await call_err(absent, "skill_read", name="auth-skill")
        await call_err(disp, "skill_read", name=f"ghost-{i}")
    lst = await call(disp, "skill_list")
    check("F1 server responsive after 120 failing calls",
          isinstance(lst.get("skills"), list), str(lst)[:100])
    e = await call_err(disp, "skill_publish", name="cjk-20000", body="z" * 20001)
    ok = False
    try:
        start = e.index("{")
        payload = json.loads(e[start:e.rindex("}") + 1])
        ok = (payload.get("error") == "skill_limit_exceeded"
              and payload.get("limit_chars") == 20000
              and payload.get("current_chars") == 20001 and "hint" in payload)
    except Exception:
        ok = False
    check("F2 over-limit error is structured JSON (error/limit/current/hint)", ok, e[:220])

    print("== G. fix2: existence side-channel + missing version row ==")
    e_before = await call_err(absent, "skill_read", name="probe-skill")
    await call(disp, "skill_publish", name="probe-skill", body="probe body")
    e_after = await call_err(absent, "skill_read", name="probe-skill")
    e_disp = await call_err(disp, "skill_read", name="ghost-skill")
    check("G1 member errors identical (nonexistent==unassigned), dispatcher precise",
          e_before == e_after and "not assigned" in e_after
          and "no such skill" not in e_after and "no such skill" in e_disp,
          f"before={e_before[:100]} after={e_after[:100]} disp={e_disp[:100]}")
    await call(disp, "skill_publish", name="fragile-skill", body="fragile body")
    await call(disp, "skill_assign", name="fragile-skill", member_name="absent")
    rd = await call(absent, "skill_read", name="fragile-skill")
    wconn = sqlite3.connect(f"{BASE}/pack.db")
    wconn.execute(
        "DELETE FROM skill_versions WHERE skill_id="
        "(SELECT id FROM skills WHERE name='fragile-skill') AND version=1")
    wconn.commit()
    wconn.close()
    e = await call_err(absent, "skill_read", name="fragile-skill")
    lst = await call(disp, "skill_list")
    check("G2 missing version row -> clean inconsistent error, no TypeError, server ok",
          rd["body"] == "fragile body" and "skill data inconsistent" in e
          and "TypeError" not in e and "NoneType" not in e
          and isinstance(lst.get("skills"), list),
          f"rd={str(rd)[:80]} err={e[:160]}")

    print(f"\n{passed}/{passed + failed} passed")
    if findings:
        print("FINDINGS:")
        for f in findings:
            print(f"  - {f}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
