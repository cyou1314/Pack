#!/usr/bin/env python3
"""Pack hub v1 (阶段 B1) — 群聊 + 任务中枢。

单文件 FastMCP 服务 + SQLite。实现 SPEC-v0 第 3/4 节协议：
  工具: member_register / room_read_new / room_post / task_create / task_claim /
        task_update / task_complete / task_block / heartbeat / task_list / task_review /
        run_start / run_finish / run_list（附录 G.1 第 4 条 Run 层 fence）/
        rule_list / rule_upsert / rule_delete（附录 G.1 第 1 条 批准规则库）/
        review_file（附录 G.1 第 2 条 独立审查者留痕）/
        egress_rule_list / egress_rule_upsert / egress_rule_delete / egress_check
        （附录 G.1 第 3 条 数据出口管制·中枢判定：站点规则 + 群主点名 + 审计）
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
import urllib.parse

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
CREATE TABLE IF NOT EXISTS egress_rules(
  id INTEGER PRIMARY KEY, pattern TEXT NOT NULL UNIQUE, effect TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS review_log(
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, target_url TEXT NOT NULL,
  owner_quote TEXT NOT NULL, member_desc TEXT NOT NULL, verdict TEXT NOT NULL,
  reviewer TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, member_id INTEGER NOT NULL,
  fence INTEGER NOT NULL, claim_token TEXT, status TEXT NOT NULL DEFAULT 'running',
  outcome TEXT, detail TEXT, started_at REAL NOT NULL, ended_at REAL,
  UNIQUE(task_id, fence));
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_one_running ON runs(task_id) WHERE status='running';
"""


def audit(conn, actor, action, detail=""):
    conn.execute("INSERT INTO audit(ts,actor,action,detail) VALUES(?,?,?,?)",
                 (time.time(), actor, action, detail))


# ---------------- Run 层防重跑（SPEC 附录 G.1 第 4 条） ----------------
# 任务层已有原子认领+租约；本层补的是「同一次执行只能跑一份」：
# 执行体认领后、动手前 run_start 登记，拿到按任务单调递增的 fence 号；
# 同一任务已有 running 记录时第二次登记被拒（run_fence_reject 审计留档）。
# 执行记录由 run_finish / task_complete / task_block / task_cancel / 租约回收关闭，
# outcome 分别为 completed/failed/aborted、completed、blocked、cancelled、reaped。
# Attempt 台账 = runs 表本体，run_list 按 fence 逐次可查。与认领/租约互补，不替代。

def _close_run(conn, task_id, outcome, actor, detail=""):
    """关闭某任务当前 running 的 run 记录并写 run_close 审计。
    没有 running 记录时返回 None（不报错，供任务生命周期钩子静默调用）。"""
    row = conn.execute(
        "SELECT * FROM runs WHERE task_id=? AND status='running'", (task_id,)).fetchone()
    if not row:
        return None
    conn.execute("UPDATE runs SET status='closed', ended_at=?, outcome=? WHERE id=?",
                 (time.time(), outcome, row["id"]))
    audit(conn, actor, "run_close",
          f"task {task_id} run {row['id']} fence={row['fence']} outcome={outcome}"
          + (f" {detail}" if detail else ""))
    return row


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


# ---------------- 数据出口管制·中枢判定（SPEC 附录 G.1 第 3 条） ----------------
# 防线不放在识别注入文本，放在管出口：成员抓任何 http(s) 前先经 egress_check
# 判定。本轮中枢只落策略与判定，执行体按结论行事（ask 就走 approval_request
# 等决议）是 Mac 侧接入的事，见 hub-staging4/DEPLOY.md。
# 判定顺序（G.1 第 3 条四类免问站点在本轮的落地）：
#   ① egress_rules 命中（host 相等或点边界后缀）→ 按规则 effect（allow/ask）；
#   ② task_id 给出且群主（dispatcher/owner）创建的该任务 title/body 里
#      出现过同站 host（相等或任一为另一的子域）→ allow（群主点名）；
#   ③ 其余一律 ask，无例外通道。
# 「本任务内已批准的」站点随批准决议落在具体任务上下文里，由执行体在
# 放行后自行放行同站后续抓取，不在中枢另建表；「例程点名」与「已污染收紧」
# （非群主发起的 run 写记忆先问）属执行体/记忆层，列入 DEPLOY 后续项。
# 每次判定（含畸形 URL 拒绝）都写审计：actor='egress'，与真人/规则/审查区分。

_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_HOSTISH_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")


def _egress_host(raw):
    """把 URL 规范化成可比对的 host：scheme 必须是 http/https（缺失/其他
    直接拒绝）；urllib 的 hostname 自动剥 userinfo 与端口并转小写；
    再剥尾点、非 ASCII 走 IDNA。畸形一律 ValueError 并说明原因。"""
    u = (raw or "").strip()
    if not u:
        raise ValueError("malformed url: empty")
    try:
        parts = urllib.parse.urlsplit(u)
    except ValueError as e:
        raise ValueError(f"malformed url: {e}")
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("malformed url: scheme must be http or https")
    host = parts.hostname
    if not host:
        raise ValueError("malformed url: no host")
    host = host.rstrip(".")
    if not host or ".." in host or any(c in host for c in " \t/\\"):
        raise ValueError(f"malformed host: {host!r}")
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            raise ValueError(f"malformed host (idna): {host!r}")
    return host


def _egress_pattern(raw):
    """规则 pattern 规范化：去空白/小写/剥 *. 前缀与尾点；必须是至少含一个点
    的合法域名（单标签 pattern 太宽，不收；IP 字面量本轮不支持）。"""
    p = (raw or "").strip().lower()
    if p.startswith("*."):
        p = p[2:]
    p = p.rstrip(".")
    labels = p.split(".") if p else []
    if len(labels) < 2 or any(not _HOST_LABEL_RE.fullmatch(x) for x in labels):
        raise ValueError(f"malformed egress pattern: {raw!r} (want a domain like example.com)")
    return p


def _host_covers(host, pattern):
    """pattern 覆盖 host：相等，或 host 是 pattern 的子域（点边界后缀）。"""
    return host == pattern or host.endswith("." + pattern)


def _egress_rule_eval(conn, host):
    """在启用的 egress_rules 里找覆盖 host 的最具体一条：
    精确相等优先，其次 pattern 最长（最深的子域规则优先）。"""
    rows = conn.execute("SELECT * FROM egress_rules WHERE enabled=1").fetchall()
    hits = [r for r in rows if _host_covers(host, r["pattern"])]
    if not hits:
        return None
    hits.sort(key=lambda r: (r["pattern"] != host, -len(r["pattern"])))
    return hits[0]


def _task_hosts(text):
    """从任务 title/body 里抽出出现过的 host 候选（小写、剥尾点）。
    正文由群主在建任务时写死、成员改不了，抽取宽松些无妨：
    宁可多抽（文件名之类），判定还要与目标 host 同站才算命中。"""
    out = set()
    for m in _HOSTISH_RE.finditer((text or "").lower()):
        out.add(m.group(0).rstrip("."))
    return out


def _task_mention(conn, task_id, host):
    """群主点名判定：任务须存在且由群主侧（dispatcher 角色或 owner 名）
    创建——成员自建任务里写 host 不算点名，防自授。其 title/body 出现过
    与 host 同站的 host（相等、或任一是另一的子域）才算点名。"""
    t = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        raise ValueError(f"no such task: {task_id}")
    creator = conn.execute("SELECT name, role FROM members WHERE id=?",
                           (t["created_by"],)).fetchone()
    if not creator or not (creator["role"] == "dispatcher" or creator["name"] == "owner"):
        return None
    for m in _task_hosts(f"{t['title']}\n{t['body']}"):
        if host == m or host.endswith("." + m) or m.endswith("." + host):
            return t
    return None


def _egress_decide(conn, url, task_id):
    """纯判定（不写审计）：返回 (host, decision, source)；
    畸形 URL / 不存在的 task_id 抛 ValueError。"""
    host = _egress_host(url)
    rule = _egress_rule_eval(conn, host)
    if rule:
        return host, rule["effect"], f"rule#{rule['id']}"
    if task_id:
        if _task_mention(conn, task_id, host):
            return host, "allow", f"task#{task_id}-mention"
    return host, "ask", "none"


# ---------------- 独立审查者（SPEC 附录 G.1 第 2 条） ----------------
# Pack 的现实版：没有自动审查模型，审查者 = 调度员的一个受限审查动作。
# 审查时只许看三样证据：目标 URL、群主原话、成员描述；永不看成员读过的
# 页面/结果/入站消息（防审查者本身被注入）。限制是结构性的——review_file
# 的入参只有这三项证据 + 结论，工具签名本身没有传页面内容的口子。
# 本条落的是留痕与闸门：审查入 review_log、决议按既有格式回房间、
# 审计 actor='reviewer' 与真人消息决议、规则自动决议区分。
# 顺序：规则库在 room_post 落库时已同步求值，未命中才轮到审查/人工；
# review_file 只受理「有 approval_request 且其后尚无决议」的待批任务，
# 已决议（规则自动 / 人工消息 / 先前审查）一律拒绝重复 filing。
# 注意：审查纪律是流程约束——工具保证的是留痕完整与字段受限；
# 调度员在别处看过什么，工具管不住，也不假装管得住。

def _approval_state(conn, task_id):
    """定位某任务最近一轮待批状态：返回 (request_msg, decided_by)。
    request_msg 为最近一条 approval_request 消息（task_id 已补写，或正文含「任务#N」），
    没有返回 None；decided_by 为其后已出现的决议来源
    （'message' = 调度员决议消息，含规则引擎代发；'review' = 先前审查留痕），未决议为 None。"""
    req = None
    rows = conn.execute(
        "SELECT * FROM messages WHERE kind='approval_request' ORDER BY id DESC").fetchall()
    for r in rows:
        if r["task_id"] == task_id:
            req = r
            break
        m = TASK_REF_RE.search(r["body"] or "")
        if m and int(m.group(1)) == task_id:
            req = r
            break
    if not req:
        return None, None
    disp = conn.execute("SELECT id FROM members WHERE role='dispatcher'").fetchone()
    if disp:
        pat = re.compile(r"^(批准|拒绝)\s*任务#%d(?!\d)" % task_id)
        after = conn.execute(
            "SELECT body FROM messages WHERE id>? AND kind='chat' AND author_id=? ORDER BY id",
            (req["id"], disp["id"])).fetchall()
        for r in after:
            if pat.match(r["body"] or ""):
                return req, "message"
    seen = conn.execute(
        "SELECT id FROM review_log WHERE task_id=? AND created_at>=?",
        (task_id, req["created_at"])).fetchone()
    if seen:
        return req, "review"
    return req, None


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
        _close_run(conn, t["id"], "reaped", "sweeper")
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
        _close_run(conn, task_id, "completed", me["name"])
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
        _close_run(conn, task_id, "blocked", me["name"])
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
        _close_run(conn, task_id, "cancelled", me["name"])
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
    def run_start(task_id: int, claim_token: str) -> dict:
        """持有人开工登记（附录 G.1 第 4 条）：认领成功后、真正动手前调用。
        同一任务同时只许一份 running 执行——已有执行在跑时本次登记被拒，
        并写 run_fence_reject 审计留档。返回 fence 号（按任务单调递增，Attempt 序号）。
        与认领/租约互补：认领防抢单，本登记防同一认领被双进程重复执行。"""
        me = authenticate()
        require(me)
        conn = db()
        try:
            with _lock:
                t = _holder(conn, me, task_id, claim_token)
                if t["status"] not in ("claimed", "working"):
                    raise ValueError(
                        f"task {task_id} not in claimed/working (status={t['status']})")
                open_run = conn.execute(
                    "SELECT * FROM runs WHERE task_id=? AND status='running'",
                    (task_id,)).fetchone()
                if open_run:
                    audit(conn, me["name"], "run_fence_reject",
                          f"task {task_id} running run {open_run['id']} fence={open_run['fence']}")
                    conn.commit()
                    raise ValueError(
                        f"run already running for task {task_id} "
                        f"(run {open_run['id']}, fence {open_run['fence']})")
                fence = conn.execute(
                    "SELECT COALESCE(MAX(fence),0)+1 AS f FROM runs WHERE task_id=?",
                    (task_id,)).fetchone()["f"]
                now = time.time()
                cur = conn.execute(
                    """INSERT INTO runs(task_id,member_id,fence,claim_token,status,started_at)
                       VALUES(?,?,?,?,'running',?)""",
                    (task_id, me["id"], fence, claim_token, now))
                rid = cur.lastrowid
                audit(conn, me["name"], "run_start", f"task {task_id} run {rid} fence={fence}")
                conn.commit()
            return {"run_id": rid, "task_id": task_id, "fence": fence,
                    "status": "running", "started_at": now}
        finally:
            conn.close()

    @mcp.tool
    def run_finish(task_id: int, claim_token: str, outcome: str = "completed",
                   detail: str = "") -> dict:
        """持有人结束本次执行：关闭 run 记录（outcome: completed/failed/aborted）。
        只关 Run 层记录，任务状态仍须 task_complete / task_block 推进；
        若先交活/报卡，run 记录会被自动以 completed/blocked 关闭，无须再调本工具。"""
        me = authenticate()
        require(me)
        if outcome not in ("completed", "failed", "aborted"):
            raise ValueError("outcome must be completed/failed/aborted")
        conn = db()
        try:
            with _lock:
                _holder(conn, me, task_id, claim_token)
                row = _close_run(conn, task_id, outcome, me["name"], detail)
                if not row:
                    raise ValueError(f"no running run for task {task_id}")
                conn.commit()
            return {"run_id": row["id"], "task_id": task_id, "fence": row["fence"],
                    "status": "closed", "outcome": outcome}
        finally:
            conn.close()

    @mcp.tool
    def run_list(task_id: int = 0, limit: int = 50) -> dict:
        """调度员查 Run 层执行台账（附录 G.1 第 4 条）：每次执行按 fence 逐次留档，
        含状态（running/closed）与 outcome（completed/failed/aborted/blocked/
        cancelled/reaped）。task_id=0 查全部任务，倒序、默认最近 50 条。"""
        me = authenticate()
        require(me, "dispatcher")
        conn = db()
        q = """SELECT r.id, r.task_id, mb.name AS member, r.fence, r.status, r.outcome,
                      r.detail, r.started_at, r.ended_at
               FROM runs r JOIN members mb ON mb.id=r.member_id WHERE 1=1"""
        args = []
        if task_id:
            q += " AND r.task_id=?"
            args.append(task_id)
        q += " ORDER BY r.id DESC LIMIT ?"
        args.append(limit)
        rows = conn.execute(q, args).fetchall()
        conn.close()
        return {"runs": [dict(x) for x in rows]}

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
    def egress_rule_list() -> dict:
        """调度员查看出口站点规则（附录 G.1 第 3 条）：全部规则，含已停用的。
        与 rule_list（批准规则库）同构，那套管「动作批不批」，这套管「站点抓不抓」"""
        me = authenticate()
        require(me, "dispatcher")
        conn = db()
        rows = conn.execute(
            "SELECT id, pattern, effect, source, enabled, created_by, created_at "
            "FROM egress_rules ORDER BY id").fetchall()
        conn.close()
        return {"rules": [dict(r) for r in rows]}

    @mcp.tool
    def egress_rule_upsert(pattern: str, effect: str, source: str = "") -> dict:
        """调度员新增/翻转一条出口站点规则。pattern 是域名（如 example.com），
        覆盖其自身与全部子域（点边界后缀比对）；effect=allow（免问可抓）|
        ask（明示每次要问，压过群主点名）。同 pattern 重复调用即更新（翻转）。"""
        me = authenticate()
        require(me, "dispatcher")
        if effect not in ("allow", "ask"):
            raise ValueError("effect must be 'allow' or 'ask'")
        pattern = _egress_pattern(pattern)
        conn = db()
        conn.execute(
            """INSERT INTO egress_rules(pattern,effect,source,enabled,created_by,created_at)
               VALUES(?,?,?,1,?,?)
               ON CONFLICT(pattern) DO UPDATE SET
                 effect=excluded.effect, source=excluded.source, enabled=1""",
            (pattern, effect, source, me["name"], time.time()))
        row = conn.execute("SELECT id FROM egress_rules WHERE pattern=?",
                           (pattern,)).fetchone()
        audit(conn, me["name"], "egress_rule_upsert", f"{pattern} = {effect}")
        conn.commit()
        conn.close()
        return {"rule_id": row["id"], "pattern": pattern, "effect": effect}

    @mcp.tool
    def egress_rule_delete(rule_id: int) -> dict:
        """调度员删除一条出口站点规则；删后该站点恢复走点名/弹问判定。"""
        me = authenticate()
        require(me, "dispatcher")
        conn = db()
        cur = conn.execute("DELETE FROM egress_rules WHERE id=?", (rule_id,))
        if cur.rowcount != 1:
            conn.close()
            raise ValueError(f"no such egress rule: {rule_id}")
        audit(conn, me["name"], "egress_rule_delete", f"egress_rule#{rule_id}")
        conn.commit()
        conn.close()
        return {"deleted": rule_id}

    @mcp.tool
    def egress_check(url: str, task_id: int = 0) -> dict:
        """成员抓取前判定（附录 G.1 第 3 条）：这个 URL 现在能不能直接抓。
        顺序：①出口规则命中 → 按规则（allow/ask）；②task_id 给出且群主在
        该任务正文里点名过同站 → allow；③其余 → ask（须走 approval_request
        等决议，无例外通道）。畸形 URL 直接报错拒绝。每次判定写审计
        （actor='egress'）。返回 decision / host / source（命中来源）。"""
        me = authenticate()
        require(me)
        conn = db()
        try:
            try:
                host, decision, source = _egress_decide(conn, url, task_id)
            except ValueError as e:
                audit(conn, "egress", "egress_check",
                      f"member={me['name']} decision=reject reason={e} url={(url or '')[:200]}")
                conn.commit()
                raise
            audit(conn, "egress", "egress_check",
                  f"member={me['name']} host={host} decision={decision} "
                  f"source={source} url={(url or '')[:200]}")
            conn.commit()
            return {"decision": decision, "host": host, "source": source,
                    "task_id": task_id}
        finally:
            conn.close()

    @mcp.tool
    def review_file(task_id: int, target_url: str, owner_quote: str,
                    member_desc: str, verdict: str) -> dict:
        """调度员以独立审查者身份对待批任务留痕决议（附录 G.1 第 2 条）。
        入参只有三项审查证据——目标 URL、群主原话、成员描述——加结论
        allow/deny；签名里刻意没有页面内容/成员结果/入站消息的位置：
        审查者永不看成员读过的东西，防审查环节本身被注入。
        顺序在规则库之后：规则已在 approval_request 落库时同步求值，
        本工具只受理尚未被规则自动决议、人工也未决议的待批任务，
        已决议的重复 filing 直接拒绝。三项证据缺一不可。
        allow 在房间留「批准 任务#N（独立审查）」、deny 留「拒绝 任务#N（独立审查）」，
        同时写 review_log 与审计（actor='reviewer'）。"""
        me = authenticate()
        require(me, "dispatcher")
        if verdict not in ("allow", "deny"):
            raise ValueError("verdict must be 'allow' or 'deny'")
        target_url = target_url.strip()
        owner_quote = owner_quote.strip()
        member_desc = member_desc.strip()
        for label, v in (("target_url", target_url), ("owner_quote", owner_quote),
                         ("member_desc", member_desc)):
            if not v:
                raise ValueError(f"missing evidence field: {label}")
        conn = db()
        try:
            t = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not t:
                raise ValueError(f"no such task: {task_id}")
            req, decided_by = _approval_state(conn, task_id)
            if not req:
                raise ValueError(
                    f"task {task_id} has no approval request on file; nothing to review")
            if decided_by:
                raise ValueError(
                    f"task {task_id} already decided (via {decided_by}); "
                    "review_file only handles undecided approvals")
            now = time.time()
            cur = conn.execute(
                """INSERT INTO review_log(task_id,target_url,owner_quote,member_desc,
                   verdict,reviewer,created_at) VALUES(?,?,?,?,?,?,?)""",
                (task_id, target_url, owner_quote, member_desc, verdict, me["name"], now))
            log_id = cur.lastrowid
            text = (f"批准 任务#{task_id}（独立审查）" if verdict == "allow"
                    else f"拒绝 任务#{task_id}（独立审查）")
            cur = conn.execute(
                "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
                "VALUES(?,?,'chat',?,?,?)",
                (t["room_id"], me["id"], text, task_id, now))
            mid = cur.lastrowid
            audit(conn, "reviewer", f"review_{verdict}",
                  f"task {task_id} review#{log_id} url={target_url}")
            conn.commit()
            return {"task_id": task_id, "verdict": verdict,
                    "review_id": log_id, "message_id": mid}
        finally:
            conn.close()

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
