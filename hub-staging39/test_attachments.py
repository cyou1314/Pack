import asyncio, base64, hashlib, json, os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, setup_members, TOKENS

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok {name}")
    else: FAIL += 1; print(f"  FAIL {name} {extra}")

PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")).decode()
GIF = base64.b64encode(b"GIF89a" + b"\x01\x00\x01\x00\x00\x00\x00" + b"\x00" * 20).decode()
JPG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 32).decode()
WEBP = base64.b64encode(b"RIFF" + b"\x10\x00\x00\x00" + b"WEBP" + b"\x00" * 8).decode()
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pack.db")
ADIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attachments")

async def main():
    await setup_members()
    A, B, D = TOKENS["alice"], TOKENS["bob"], TOKENS["dispatcher"]

    ok, r = await call(A, "attachment_put", room="pack-main", filename="dot.png", mime="image/png", data_base64=PNG)
    check("put png", ok and "attachment_id" in r, str(r))
    aid1 = r["attachment_id"]
    sha_png = hashlib.sha256(base64.b64decode(PNG)).hexdigest()
    check("sha matches", r["sha256"] == sha_png)
    check("file stored once", os.path.exists(os.path.join(ADIR, sha_png[:2], sha_png + ".png")))

    ok, r2 = await call(B, "attachment_put", room="pack-main", filename="copy.png", mime="image/png", data_base64=PNG)
    check("dedupe put by other member", ok and r2["sha256"] == sha_png and r2["attachment_id"] != aid1, str(r2))
    aid_dup = r2["attachment_id"]
    files = []
    for root, _, fs in os.walk(ADIR):
        files += [f for f in fs if f == sha_png + ".png"]
    check("dedupe single file on disk", len(files) == 1, str(files))

    ok, r = await call(B, "attachment_get", attachment_id=aid1)
    check("get roundtrip", ok and r["data_base64"] == PNG and r["mime"] == "image/png", str(r)[:80])
    ok, r = await call(A, "attachment_get", attachment_id=99999)
    check("get missing rejected", not ok)
    ok, r = await call("bad-token", "attachment_get", attachment_id=aid1)
    check("get bad token rejected", not ok)

    ok, r = await call(A, "attachment_put", room="pack-main", filename="x.png", mime="image/gif", data_base64=PNG)
    check("mime/magic mismatch rejected", not ok)
    ok, r = await call(A, "attachment_put", room="pack-main", filename="x.txt", mime="text/plain", data_base64=PNG)
    check("non-image mime rejected", not ok)
    ok, r = await call(A, "attachment_put", room="pack-main", filename="x.png", mime="image/png", data_base64="!!!not-b64!!!")
    check("invalid base64 rejected", not ok)
    ok, r = await call(A, "attachment_put", room="pack-main", filename="x.png", mime="image/png", data_base64="")
    check("empty rejected", not ok)
    big = "A" * ((10 * 1024 * 1024 * 4 // 3) + 2048)
    ok, r = await call(A, "attachment_put", room="pack-main", filename="big.png", mime="image/png", data_base64=big)
    check("oversize rejected at pre-check", not ok)
    ok, r = await call(A, "attachment_put", room="no-such-room", filename="x.png", mime="image/png", data_base64=PNG)
    check("put to missing room rejected", not ok)

    for nm, b64, mime in (("g.gif", GIF, "image/gif"), ("j.jpg", JPG, "image/jpeg"), ("w.webp", WEBP, "image/webp")):
        ok, r = await call(A, "attachment_put", room="pack-main", filename=nm, mime=mime, data_base64=b64)
        check(f"put {mime}", ok, str(r)[:80])

    ok, r = await call(A, "attachment_put", room="pack-main", filename="pic.png", mime="image/png", data_base64=PNG)
    aid2 = r["attachment_id"]
    ok, m = await call(A, "room_post", room="pack-main", body="with pic", attachment_ids=json.dumps([aid2]))
    check("post with attachment", ok and "message_id" in m, str(m))
    mid = m["message_id"]
    ok, rr = await call(B, "room_read_new", room="pack-main", since_id=mid - 1, limit=5)
    msg = [x for x in rr["messages"] if x["id"] == mid]
    check("read_new carries attachment meta", bool(msg) and msg[0]["attachments"] and msg[0]["attachments"][0]["id"] == aid2, str(rr)[:200])
    ok, pm = await call(B, "room_post", room="pack-main", body="plain one")
    ok, plain = await call(B, "room_read_new", room="pack-main", since_id=pm["message_id"] - 1, limit=2)
    pmsg = [x for x in plain["messages"] if x["id"] == pm["message_id"]]
    check("plain messages have empty attachments", bool(pmsg) and pmsg[0]["attachments"] == [])

    ok, r = await call(A, "room_post", room="pack-main", body="rebind", attachment_ids=json.dumps([aid2]))
    check("rebind rejected", not ok)
    ok, r = await call(B, "room_post", room="pack-main", body="steal", attachment_ids=json.dumps([aid1]))
    check("bind other's upload rejected", not ok)
    ok, r = await call(A, "room_post", room="pack-main", body="ghost", attachment_ids=json.dumps([99999]))
    check("bind missing id rejected", not ok)
    ok, r = await call(A, "room_post", room="pack-main", body="badjson", attachment_ids="not-json")
    check("bind malformed json rejected", not ok)
    ok, r = await call(A, "room_post", room="pack-main", body="dup", attachment_ids=json.dumps([aid1, aid1]))
    check("bind duplicate ids rejected", not ok)
    # failed binds must not leave orphan messages behind
    conn = sqlite3.connect(DB)
    n = conn.execute("SELECT COUNT(*) FROM messages WHERE body IN ('rebind','steal','ghost','badjson','dup')").fetchone()[0]
    conn.close()
    check("failed binds left no messages", n == 0, f"orphans={n}")

    ok, lst = await call(B, "attachment_list", room="pack-main", message_id=mid)
    check("list by message", ok and len(lst["attachments"]) == 1 and lst["attachments"][0]["id"] == aid2, str(lst)[:160])
    ok, lst = await call(B, "attachment_list", room="pack-main")
    check("list room", ok and len(lst["attachments"]) >= 5, str(len(lst.get("attachments", []))))

    # concurrency: 6 parallel puts of distinct images + parallel bind race on one attachment
    async def put(i):
        b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 64).decode()
        return await call(A, "attachment_put", room="pack-main", filename=f"c{i}.png", mime="image/png", data_base64=b64)
    res = await asyncio.gather(*[put(i) for i in range(6)])
    check("6 parallel puts all succeed", all(x[0] for x in res), str(res)[:200])

    ok, r = await call(A, "attachment_put", room="pack-main", filename="race.png", mime="image/png", data_base64=PNG)
    race_aid = r["attachment_id"]
    async def bind(tag):
        return await call(A, "room_post", room="pack-main", body=tag, attachment_ids=json.dumps([race_aid]))
    res = await asyncio.gather(*[bind(f"race{i}") for i in range(4)])
    wins = sum(1 for x in res if x[0])
    check("parallel bind race: exactly one wins", wins == 1, f"wins={wins}")

    # existing guards unaffected
    ok, r = await call(A, "room_post", room="pack-main", body="x", kind="task_card", attachment_ids=json.dumps([aid_dup]))
    check("task_card guard still rejects", not ok)

    print(f"\n{PASS} passed, {FAIL} failed")
    return FAIL

sys.exit(1 if asyncio.run(main()) else 0)
