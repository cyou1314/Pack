#!/usr/bin/env python3
"""Pack hub v1 (阶段 B1) — 群聊 + 任务中枢。

单文件 FastMCP 服务 + SQLite。实现 SPEC-v0 第 3/4 节协议：
  工具: member_register / room_read_new / room_post / task_create / task_claim /
        task_update / task_complete / task_block / heartbeat / task_list / task_review
  任务状态机: open -> claimed -> working -> review -> done
              旁路: blocked / released(回池) / cancelled
  认领原子性: 单条 UPDATE ... WHERE status='open' 决胜负，输家 rowcount=0。
  租约: 默认 30 分钟，心跳/更新续租；后台清道夫每 60 秒回收过期租约，
        同一任务最多 3 次尝试，超限转 blocked 并写审计。
  身份: 每成员一个 Bearer token（DB 只存 sha256），dispatcher 角色唯一关单。
用法:
  python hub.py init     # 首次初始化：建库、建默认房间、生成调度员 token 到 .dispatcher_token (0600)
  python hub.py serve    # 起服务 127.0.0.1:8794 /mcp
"""
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "pack.db")
PORT = int(os.environ.get("PACK_HUB_PORT", "8794"))
LEASE_SECONDS = int(os.environ.get("PACK_LEASE_SECONDS", "1800"))
MAX_ATTEMPTS = 3

_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS members(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, role TEXT NOT NULL,
  token_hash TEXT NOT NULL, profile TEXT, last_heartbeat REAL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rooms(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL, author_id INTEGER NOT NULL,
  kind TEXT NOT NULL, body TEXT NOT NULL, task_id INTEGER, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
  created_by INTEGER NOT NULL, assignee INTEGER, status TEXT NOT NULL,
  claim_token TEXT, lease_expires REAL, attempts INTEGER NOT NULL DEFAULT 0,
  result TEXT, review_note TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS approval_rules(
  id INTEGER PRIMARY KEY, action TEXT NOT NULL DEFAULT '', target TEXT NOT NULL DEFAULT '',
  effect TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(action, target));
"""


def audit(conn, actor, action, detail=""):
    conn.execute("INSERT INTO audit(ts,actor,action,detail) VALUES(?,?,?,?)",
                 (time.time(), actor, action, detail))


# ---------------- 批准规则库（SPEC 附录 G.1 第 1 条） ----------------
# 待批动作在现状里的标识：任务。运行时认领带【需批准】标记的任务后，
# 经 room_post(kind='approval_request', body='任务#N「标题」需要群主批准…') 发起，
# 调度员回 body 前缀「批准 任务#N」/「拒绝 任务#N」的消息决议（运行时与 App 的既有约定）。
# 规则键 = 动作类型 + 目标：
#   动作类型 = 任务标题里第一个半角方括号标记（[ENG]/[M4A]/[ENG-image] 是既有标记体系），无标记为 ''；
#   目标     = 任务的被指派成员名，未指派为 ''。键里 '' 表示通配。

TASK_REF_RE = re.compile(r"任务#(\d+)")
ACTION_TAG_RE = re.compile(r"\[([A-Za-z0-9_-]+)\]")


def action_tag(title):
    m = ACTION_TAG_RE.search(title or "")
    return f"[{m.group(1)}]" if m else ""


def rule_eval(conn, action, target):
    """按具体度查规则库：(action,target) > (action,'') > ('',target) > ('','')，命中返回 row。"""
    for a, t in ((action, target), (action, ""), ("", target), ("", "")):
        row = conn.execute(
            "SELECT * FROM approval_rules WHERE action=? AND target=? AND enabled=1",
            (a, t)).fetchone()
        if row:
            return row
    return None


def _approval_gate(conn, room_id, msg_id, body):
    """approval_request 落库后的规则求值（room_post 调用，同一事务内）。
    命中 allow/block：以调度员身份按既有决议格式留决议消息（运行时/App 立刻认），
    另以 actor='rule-engine' 写审计区分真人决议；未命中：不做任何事，走原有弹问流程。
    顺带把消息的 task_id 补上（原 room_post 不写，App 批准中心按 task_id 过滤待批项）。"""
    m = TASK_REF_RE.search(body or "")
    if not m:
        return {}
    tid = int(m.group(1))
    conn.execute("UPDATE messages SET task_id=? WHERE id=?", (tid, msg_id))
    t = conn.execute(
        """SELECT t.title, mb.name AS assignee_name FROM tasks t
           LEFT JOIN members mb ON mb.id=t.assignee WHERE t.id=?""", (tid,)).fetchone()
    if not t:
        return {}
    action = action_tag(t["title"])
    target = t["assignee_name"] or ""
    rule = rule_eval(conn, action, target)
    if not rule:
        return {}
    disp = conn.execute("SELECT id FROM members WHERE role='dispatcher'").fetchone()
    if not disp:
        return {}
    tag = f"{action or '无标记'}→{target or '任意成员'} {rule['effect']}，规则#{rule['id']}"
    if rule["effect"] == "allow":
        text, act = f"批准 任务#{tid}（规则库自动通过：{tag}）", "approval_auto_allow"
    else:
        text, act = f"拒绝 任务#{tid}（规则库自动拒绝：{tag}）", "approval_auto_block"
    conn.execute(
        "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) VALUES(?,?,'chat',?,?,?)",
        (room_id, disp["id"], text, tid, time.time()))
    audit(conn, "rule-engine", act, f"task {tid} rule#{rule['id']} {action}->{target}")
    return {"auto_decision": rule["effect"], "rule_id": rule["id"], "task_id": tid}


def hash_token(tok):
    return hashlib.sha256(tok.encode()).hexdigest()



AVATAR_PALETTE = [
    ("#8B5CF6", "#6366F1"), ("#0EA5E9", "#22C55E"), ("#F59E0B", "#EF4444"),
    ("#EC4899", "#8B5CF6"), ("#14B8A6", "#0EA5E9"), ("#F97316", "#F59E0B"),
    ("#6366F1", "#0EA5E9"), ("#84CC16", "#14B8A6"),
]

def avatar_for(name: str) -> dict:
    """Deterministic designed avatar for a member: identity gradient picked
    by a stable hash of the name, plus a monogram glyph. Assigned once at
    registration and backfilled for existing members, so every client
    renders the same face for the same member (GrokBot-style auto avatar)."""
    h = hashlib.md5(name.encode("utf-8")).digest()[0]
    c1, c2 = AVATAR_PALETTE[h % len(AVATAR_PALETTE)]
    glyph = name[0].upper() if name and name[0].isascii() else (name[0] if name else "?")
    return {"glyph": glyph, "c1": c1, "c2": c2}

def init():
    conn = db()
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO rooms(name,created_at) VALUES('pack-main',?)", (time.time(),))
    row = conn.execute("SELECT id FROM members WHERE role='dispatcher'").fetchone()
    if not row:
        tok = "pk-disp-" + secrets.token_urlsafe(32)
        conn.execute("INSERT INTO members(name,role,token_hash,created_at,avatar) VALUES('dispatcher','dispatcher',?,?,?)",
                     (hash_token(tok), time.time(), json.dumps(avatar_for("dispatcher"))))
        audit(conn, "system", "init", "dispatcher created")
        with open(os.path.join(BASE, ".dispatcher_token"), "w") as f:
            f.write(tok)
        os.chmod(os.path.join(BASE, ".dispatcher_token"), 0o600)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(members)").fetchall()]
    if "avatar" not in cols:
        conn.execute("ALTER TABLE members ADD COLUMN avatar TEXT")
    for r in conn.execute("SELECT id, name FROM members WHERE avatar IS NULL").fetchall():
        conn.execute("UPDATE members SET avatar=? WHERE id=?",
                     (json.dumps(avatar_for(r["name"])), r["id"]))
    conn.commit()
    conn.close()
    print("init ok")


# ---------------- auth ----------------

def authenticate():
    """从 HTTP Authorization 头解析身份，返回 member 行 dict 或 None。"""
    try:
        from fastmcp.server.dependencies import get_http_headers
        headers = get_http_headers(include={"authorization"})
    except Exception:
        return None
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    tok = auth[7:].strip()
    conn = db()
    row = conn.execute("SELECT * FROM members WHERE token_hash=?", (hash_token(tok),)).fetchone()
    conn.close()
    return dict(row) if row else None


def require(me, role=None):
    if me is None:
        raise ValueError("unauthorized: missing or bad token")
    if role and me["role"] != role:
        raise ValueError(f"forbidden: requires role {role}")


# ---------------- sweeper ----------------

def sweep_once():
    now = time.time()
    conn = db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status IN ('claimed','working') AND lease_expires IS NOT NULL AND lease_expires<?",
        (now,)).fetchall()
    for t in rows:
        if t["attempts"] >= MAX_ATTEMPTS:
            conn.execute("UPDATE tasks SET status='blocked', claim_token=NULL, lease_expires=NULL, updated_at=? WHERE id=?",
                         (now, t["id"]))
            audit(conn, "sweeper", "task_blocked_max_attempts", f"task {t['id']}")
        else:
            conn.execute("UPDATE tasks SET status='open', assignee=NULL, claim_token=NULL, lease_expires=NULL, updated_at=? WHERE id=?",
                         (now, t["id"]))
            audit(conn, "sweeper", "lease_expired_requeue", f"task {t['id']} attempts={t['attempts']}")
    conn.commit()
    conn.close()
    return len(rows)


def sweeper():
    while True:
        try:
            sweep_once()
        except Exception:
            pass
        time.sleep(60)


# ---------------- MCP tools ----------------

def build_server():
    from fastmcp import FastMCP
    # FastMCP 4 内部已禁用 SDK 的 DNS 重绑定保护、Host 守卫默认关；
    # 中枢真正的门是每成员 Bearer token（见 authenticate）。
    mcp = FastMCP("pack-hub")

    @mcp.tool
    def member_register(name: str, profile: str = "") -> dict:
        """调度员登记一个新成员，返回其一次性明文 token（中枢只存哈希）。"""
        me = authenticate()
        require(me, "dispatcher")
        tok = "pk-mem-" + secrets.token_urlsafe(32)
        conn = db()
        try:
            cur = conn.execute(
                "INSERT INTO members(name,role,token_hash,profile,created_at,avatar) VALUES(?,'member',?,?,?,?)",
                (name, hash_token(tok), profile or None, time.time(), json.dumps(avatar_for(name))))
            mid = cur.lastrowid
            audit(conn, me["name"], "member_register", name)
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            raise ValueError(f"member name already exists: {name}")
        conn.close()
        return {"member_id": mid, "name": name, "token": tok}

    @mcp.tool
    def room_post(room: str, body: str, kind: str = "chat") -> dict:
        """在房间发一条消息。kind: chat/system/report/approval_request。"""
        me = authenticate()
        require(me)
        conn = db()
        r = conn.execute("SELECT id FROM rooms WHERE name=?", (room,)).fetchone()
        if not r:
            conn.close()
            raise ValueError(f"no such room: {room}")
        cur = conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,created_at) VALUES(?,?,?,?,?)",
            (r["id"], me["id"], kind, body, time.time()))
        mid = cur.lastrowid
        extra = {}
        if kind == "approval_request":
            extra = _approval_gate(conn, r["id"], mid, body)
        conn.commit()
        conn.close()
        out = {"message_id": mid}
        out.update(extra)
        return out

    @mcp.tool
    def room_read_new(room: str, since_id: int = 0, limit: int = 50) -> dict:
        """读房间里 id 大于 since_id 的新消息（含作者名）。"""
        me = authenticate()
        require(me)
        conn = db()
        r = conn.execute("SELECT id FROM rooms WHERE name=?", (room,)).fetchone()
        if not r:
            conn.close()
            raise ValueError(f"no such room: {room}")
        rows = conn.execute(
            """SELECT m.id, mb.name AS author, m.kind, m.body, m.task_id, m.created_at
               FROM messages m JOIN members mb ON mb.id=m.author_id
               WHERE m.room_id=? AND m.id>? ORDER BY m.id ASC LIMIT ?""",
            (r["id"], since_id, limit)).fetchall()
        conn.close()
        return {"messages": [dict(x) for x in rows]}

    @mcp.tool
    def task_create(room: str, title: str, body: str, assignee_name: str = "") -> dict:
        """调度员建任务卡。assignee_name 非空 = 直接指派（仍需成员 claim 才算数）。"""
        me = authenticate()
        require(me, "dispatcher")
        conn = db()
        r = conn.execute("SELECT id FROM rooms WHERE name=?", (room,)).fetchone()
        if not r:
            conn.close()
            raise ValueError(f"no such room: {room}")
        assignee = None
        if assignee_name:
            a = conn.execute("SELECT id FROM members WHERE name=?", (assignee_name,)).fetchone()
            if not a:
                conn.close()
                raise ValueError(f"no such member: {assignee_name}")
            assignee = a["id"]
        now = time.time()
        cur = conn.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,created_at,updated_at) VALUES(?,?,?,?,?,'open',?,?)",
            (r["id"], title, body, me["id"], assignee, now, now))
        tid = cur.lastrowid
        conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) VALUES(?,?,'task_card',?,?,?)",
            (r["id"], me["id"], f"任务#{tid} {title}", tid, now))
        audit(conn, me["name"], "task_create", f"task {tid}: {title}")
        conn.commit()
        conn.close()
        return {"task_id": tid, "status": "open", "assignee": assignee_name or None}

    @mcp.tool
    def task_claim(task_id: int) -> dict:
        """认领任务（原子）：只有当前为 open 且（未指派或指派给我）时成功。返回 claim_token，后续更新凭它。"""
        me = authenticate()
        require(me)
        conn = db()
        tok = secrets.token_urlsafe(24)
        now = time.time()
        with _lock:
            cur = conn.execute(
                """UPDATE tasks SET status='claimed', assignee=?, claim_token=?,
                   lease_expires=?, attempts=attempts+1, updated_at=?
                   WHERE id=? AND status='open' AND (assignee IS NULL OR assignee=?)""",
                (me["id"], tok, now + LEASE_SECONDS, now, task_id, me["id"]))
            won = cur.rowcount == 1
            if won:
                audit(conn, me["name"], "task_claim", f"task {task_id}")
            conn.commit()
        t = conn.execute("SELECT status, assignee FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()
        if not t:
            raise ValueError(f"no such task: {task_id}")
        if not won:
            raise ValueError(f"claim failed: task {task_id} status={t['status']}")
        return {"task_id": task_id, "status": "claimed", "claim_token": tok,
                "lease_expires": now + LEASE_SECONDS}

    def _holder(conn, me, task_id, claim_token):
        t = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            raise ValueError(f"no such task: {task_id}")
        if t["assignee"] != me["id"] or not t["claim_token"] or t["claim_token"] != claim_token:
            raise ValueError("not the current holder (claim_token mismatch)")
        return t

    @mcp.tool
    def task_update(task_id: int, claim_token: str, note: str = "") -> dict:
        """持有人汇报进度：状态转 working 并续租，可附一句进度说明（进房间消息）。"""
        me = authenticate()
        require(me)
        conn = db()
        _holder(conn, me, task_id, claim_token)
        now = time.time()
        conn.execute("UPDATE tasks SET status='working', lease_expires=?, updated_at=? WHERE id=?",
                     (now + LEASE_SECONDS, now, task_id))
        if note:
            t = conn.execute("SELECT room_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            conn.execute(
                "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) VALUES(?,?,'report',?,?,?)",
                (t["room_id"], me["id"], note, task_id, now))
        conn.commit()
        conn.close()
        return {"task_id": task_id, "status": "working", "lease_expires": now + LEASE_SECONDS}

    @mcp.tool
    def task_complete(task_id: int, claim_token: str, result: str) -> dict:
        """持有人交活：状态转 review，等调度员验收关单。"""
        me = authenticate()
        require(me)
        conn = db()
        _holder(conn, me, task_id, claim_token)
        now = time.time()
        conn.execute(
            "UPDATE tasks SET status='review', result=?, claim_token=NULL, lease_expires=NULL, updated_at=? WHERE id=?",
            (result, now, task_id))
        t = conn.execute("SELECT room_id FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) VALUES(?,?,'report',?,?,?)",
            (t["room_id"], me["id"], f"交活 任务#{task_id}: {result}", task_id, now))
        audit(conn, me["name"], "task_complete", f"task {task_id}")
        conn.commit()
        conn.close()
        return {"task_id": task_id, "status": "review"}

    @mcp.tool
    def task_block(task_id: int, claim_token: str, reason: str) -> dict:
        """持有人报卡住：状态转 blocked 并说明原因，等调度员处理。"""
        me = authenticate()
        require(me)
        conn = db()
        _holder(conn, me, task_id, claim_token)
        now = time.time()
        conn.execute(
            "UPDATE tasks SET status='blocked', result=?, claim_token=NULL, lease_expires=NULL, updated_at=? WHERE id=?",
            (reason, now, task_id))
        audit(conn, me["name"], "task_block", f"task {task_id}: {reason}")
        conn.commit()
        conn.close()
        return {"task_id": task_id, "status": "blocked"}

    @mcp.tool
    def task_review(task_id: int, approve: bool, note: str = "") -> dict:
        """调度员验收：approve=True 关单(done)；False 打回回池(open, attempts 保留)。"""
        me = authenticate()
        require(me, "dispatcher")
        conn = db()
        t = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            conn.close()
            raise ValueError(f"no such task: {task_id}")
        if t["status"] != "review":
            conn.close()
            raise ValueError(f"task {task_id} not in review (status={t['status']})")
        now = time.time()
        if approve:
            conn.execute("UPDATE tasks SET status='done', review_note=?, updated_at=? WHERE id=?",
                         (note, now, task_id))
        else:
            conn.execute(
                "UPDATE tasks SET status='open', assignee=NULL, review_note=?, updated_at=? WHERE id=?",
                (note, now, task_id))
        audit(conn, me["name"], "task_review", f"task {task_id} approve={approve}")
        conn.commit()
        conn.close()
        return {"task_id": task_id, "status": "done" if approve else "open"}

    @mcp.tool
    def task_cancel(task_id: int) -> dict:
        """dispatcher cancels an unfinished task: status=cancelled, claim freed."""
        me = authenticate()
        require(me, "dispatcher")
        conn = db()
        t = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            conn.close()
            raise ValueError(f"no such task: {task_id}")
        if t["status"] in ("done", "cancelled"):
            conn.close()
            return {"task_id": task_id, "status": t["status"]}
        conn.execute(
            "UPDATE tasks SET status='cancelled', claim_token=NULL, lease_expires=NULL, updated_at=? WHERE id=?",
            (time.time(), task_id))
        audit(conn, me["name"], "task_cancel", f"task {task_id}")
        conn.commit()
        conn.close()
        return {"task_id": task_id, "status": "cancelled"}

    @mcp.tool
    def member_list() -> dict:
        """List room members with role/profile and last heartbeat (epoch seconds)."""
        me = authenticate()
        require(me)
        conn = db()
        rows = conn.execute(
            "SELECT name, role, profile, created_at, last_heartbeat, avatar FROM members ORDER BY id"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["avatar"] = json.loads(d["avatar"]) if d.get("avatar") else avatar_for(d["name"])
            except Exception:
                d["avatar"] = avatar_for(d["name"])
            out.append(d)
        return {"members": out}

    @mcp.tool
    def task_list(status: str = "", assignee_name: str = "") -> dict:
        """列任务（可按状态/被指派人过滤）。运行时靠它找指派给自己成员的活。"""
        me = authenticate()
        require(me)
        conn = db()
        q = """SELECT t.id, t.title, t.body, t.status, mb.name AS assignee, t.attempts,
                      t.result, t.lease_expires, t.updated_at
               FROM tasks t LEFT JOIN members mb ON mb.id=t.assignee WHERE 1=1"""
        args = []
        if status:
            q += " AND t.status=?"
            args.append(status)
        if assignee_name:
            q += " AND mb.name=?"
            args.append(assignee_name)
        q += " ORDER BY t.id ASC LIMIT 100"
        rows = conn.execute(q, args).fetchall()
        conn.close()
        return {"tasks": [dict(x) for x in rows]}

    @mcp.tool
    def rule_list() -> dict:
        """调度员查看批准规则库（附录 G.1）：全部规则，含已停用的。"""
        me = authenticate()
        require(me, "dispatcher")
        conn = db()
        rows = conn.execute(
            "SELECT id, action, target, effect, source, enabled, created_by, created_at "
            "FROM approval_rules ORDER BY id").fetchall()
        conn.close()
        return {"rules": [dict(r) for r in rows]}

    @mcp.tool
    def rule_upsert(action: str, target: str, effect: str, source: str = "") -> dict:
        """调度员新增/翻转一条批准规则。键=(action, target)：action 是任务标题的方括号
        动作标记（如 [ENG-image]），'' 表示任意动作；target 是被指派成员名，'' 表示任意
        成员。effect=allow|block。同键重复调用即更新该规则（翻转）。"""
        me = authenticate()
        require(me, "dispatcher")
        if effect not in ("allow", "block"):
            raise ValueError("effect must be 'allow' or 'block'")
        action, target = action.strip(), target.strip()
        conn = db()
        conn.execute(
            """INSERT INTO approval_rules(action,target,effect,source,enabled,created_by,created_at)
               VALUES(?,?,?,?,1,?,?)
               ON CONFLICT(action,target) DO UPDATE SET
                 effect=excluded.effect, source=excluded.source, enabled=1""",
            (action, target, effect, source, me["name"], time.time()))
        row = conn.execute("SELECT id FROM approval_rules WHERE action=? AND target=?",
                           (action, target)).fetchone()
        audit(conn, me["name"], "rule_upsert", f"{action}->{target} = {effect}")
        conn.commit()
        conn.close()
        return {"rule_id": row["id"], "action": action, "target": target, "effect": effect}

    @mcp.tool
    def rule_delete(rule_id: int) -> dict:
        """调度员删除一条批准规则；删后同键动作恢复走原有弹问流程。"""
        me = authenticate()
        require(me, "dispatcher")
        conn = db()
        cur = conn.execute("DELETE FROM approval_rules WHERE id=?", (rule_id,))
        if cur.rowcount != 1:
            conn.close()
            raise ValueError(f"no such rule: {rule_id}")
        audit(conn, me["name"], "rule_delete", f"rule#{rule_id}")
        conn.commit()
        conn.close()
        return {"deleted": rule_id}

    @mcp.tool
    def heartbeat() -> dict:
        """成员心跳：刷新在线时间（调度侧据此判断成员在不在）。"""
        me = authenticate()
        require(me)
        conn = db()
        conn.execute("UPDATE members SET last_heartbeat=? WHERE id=?", (time.time(), me["id"]))
        conn.commit()
        conn.close()
        return {"ok": True, "member": me["name"]}

    return mcp


def serve():
    init()
    threading.Thread(target=sweeper, daemon=True).start()
    mcp = build_server()
    mcp.run(transport="http", host=os.environ.get("PACK_HUB_HOST", "127.0.0.1"), port=PORT, path="/mcp")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "init":
        init()
    elif cmd == "sweep":
        init()
        print("requeued:", sweep_once())
    else:
        serve()
