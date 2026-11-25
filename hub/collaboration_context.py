"""Explicit, version-pinned text context for one collaboration step.

Internal Hub API: actor_id and member_id MUST come from authenticated server
identity, never from an Agent payload. No file bytes, personal memories, account
history or secrets are fetched. A grant is not a Provider egress authorization.
"""
import hashlib
import json
import uuid

from collaboration_store import _require_write, _time

MAX_ITEMS = 8
MAX_BYTES = 16 * 1024


def _owner(conn, actor_id):
    row = conn.execute(
        "SELECT name,role,kind,removed_at FROM members WHERE id=?", (actor_id,)
    ).fetchone()
    if (not row or row[0] != "owner" or row[1] != "member"
            or row[2] == "external" or row[3] is not None):
        raise ValueError("collaboration context requires owner authorization")


# Internal-only provisioning: account_ref is an opaque local UUID, never
# an email, API key, cookie, token or Provider account ID. A trusted adapter
# will later verify it against the actual account/session on this device.
def register_executor(conn, *, actor_id, member_id, provider, access_kind, account_ref):
    _require_write(conn)
    _owner(conn, actor_id)
    if (type(member_id) is not int or member_id <= 0 or not isinstance(provider,str)
            or not provider or len(provider)>64 or access_kind not in ("native","api")):
        raise ValueError("invalid collaboration executor identity")
    try:
        if str(uuid.UUID(account_ref)) != account_ref:
            raise ValueError("noncanonical UUID")
    except (ValueError, TypeError, AttributeError):
        raise ValueError("account_ref must be an opaque canonical UUID") from None
    if _recipient(conn,member_id,str(member_id)) != provider:
        raise ValueError("executor provider mismatch")
    existing = conn.execute(
        "SELECT owner_id,provider,access_kind,account_ref,revoked FROM collaboration_executors "
        "WHERE member_id=?", (member_id,)).fetchone()
    if existing:
        if tuple(existing) != (actor_id,provider,access_kind,account_ref,0):
            raise ValueError("executor binding is immutable or revoked")
        return False
    # Never convert an already used general-purpose account token in place.
    if (conn.execute("SELECT 1 FROM tasks WHERE assignee=? OR created_by=? LIMIT 1",
                     (member_id,member_id)).fetchone()
            or conn.execute("SELECT 1 FROM messages WHERE author_id=? LIMIT 1",
                            (member_id,)).fetchone()
            or conn.execute("SELECT 1 FROM collaboration_steps WHERE member=? LIMIT 1",
                            (str(member_id),)).fetchone()):
        raise ValueError("use a fresh collaboration member")
    if conn.execute("SELECT 1 FROM collaboration_executors WHERE provider=? AND access_kind=? AND account_ref=?",
                    (provider,access_kind,account_ref)).fetchone():
        raise ValueError("executor account reference already bound")
    conn.execute(
        "INSERT INTO collaboration_executors(member_id,owner_id,provider,access_kind,account_ref)"
        " VALUES(?,?,?,?,?)", (member_id,actor_id,provider,access_kind,account_ref))
    return True


def revoke_executor(conn, *, actor_id, member_id):
    _require_write(conn)
    _owner(conn,actor_id)
    return bool(conn.execute(
        "UPDATE collaboration_executors SET revoked=1 WHERE member_id=? AND owner_id=? AND revoked=0",
        (member_id,actor_id)).rowcount)


def executor_binding(conn, member_id):
    """None = ordinary member; revoked rows remain restricted permanently."""
    return conn.execute(
        "SELECT owner_id,provider,access_kind,account_ref,revoked FROM collaboration_executors "
        "WHERE member_id=?", (member_id,)).fetchone()


def require_executor(conn, member_id):
    binding = executor_binding(conn,member_id)
    if not binding or binding[4]:
        raise ValueError("collaboration executor disabled")
    _owner(conn,binding[0])
    if _recipient(conn,member_id,str(member_id)) != binding[1]:
        raise ValueError("collaboration executor provider changed")
    return binding


def _step(conn, run_id, step_key, now):
    _time(now)
    row = conn.execute("""
        SELECT r.room_id,r.source_message_id,r.status,r.deadline,s.member,s.status
        FROM collaboration_runs r JOIN collaboration_steps s ON s.run_id=r.id
        JOIN rooms rm ON rm.id=r.room_id
        WHERE r.id=? AND s.step_key=? AND rm.archived_at IS NULL
        """, (run_id, step_key)).fetchone()
    if not row or row[2] != "active" or row[3] <= now:
        raise ValueError("collaboration context is no longer active")
    return row


def _recipient(conn, member_id, member_key):
    row = conn.execute(
        "SELECT name,role,kind,removed_at,provider,access_mode FROM members WHERE id=?",
        (member_id,)).fetchone()
    if (type(member_id) is not int or str(member_id) != member_key or not row
            or row[0] == "owner" or row[1] != "member" or row[2] == "external"
            or row[3] is not None or not row[4] or row[5] == "readonly"):
        raise ValueError("collaboration context recipient is not eligible")
    return row[4]


def _required_results(conn, run_id, step_key):
    rows = conn.execute("""
        SELECT d.prerequisite,l.task_id,p.status FROM collaboration_dependencies d
        LEFT JOIN collaboration_task_links l ON l.run_id=d.run_id AND l.step_key=d.prerequisite
        LEFT JOIN collaboration_steps p ON p.run_id=d.run_id AND p.step_key=d.prerequisite
        WHERE d.run_id=? AND d.step_key=? ORDER BY d.prerequisite
        """, (run_id, step_key)).fetchall()
    if any(row[1] is None or row[2] != "succeeded" for row in rows):
        raise ValueError("collaboration prerequisites are not approved")
    return {row[1] for row in rows}


def _source(conn, *, run_id, step_key, room, owner_id, kind, source_id):
    if kind == "owner_message":
        row = conn.execute(
            "SELECT body,edited_at,created_at FROM messages WHERE id=? AND room_id=? "
            "AND author_id=? AND kind='chat' AND deleted_at IS NULL",
            (source_id, room, owner_id)).fetchone()
    elif kind == "dependency_result":
        row = conn.execute("""
            SELECT t.result,t.updated_at,t.assignee FROM tasks t
            JOIN collaboration_task_links l ON l.task_id=t.id
            JOIN collaboration_dependencies d ON d.run_id=l.run_id AND d.prerequisite=l.step_key
            JOIN collaboration_steps s ON s.run_id=l.run_id AND s.step_key=l.step_key
            WHERE t.id=? AND t.room_id=? AND d.run_id=? AND d.step_key=?
              AND t.status='done' AND s.status='succeeded' AND t.assignee=l.member_id
            """, (source_id, room, run_id, step_key)).fetchone()
    else:
        raise ValueError("unsupported collaboration context source")
    if not row or not isinstance(row[0], str) or not row[0].strip():
        raise ValueError("collaboration context source unavailable")
    text = row[0]
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("collaboration context byte budget exceeded")
    digest = hashlib.sha256(json.dumps(
        [kind, source_id, *tuple(row)], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return text, digest


def _resolve(conn, run_id, step_key, row, owner_id, refs):
    items = []
    for kind, source_id in refs:
        text, digest = _source(conn, run_id=run_id, step_key=step_key, room=row[0],
                               owner_id=owner_id, kind=kind, source_id=source_id)
        items.append(dict(kind=kind, source_id=source_id, sha256=digest, text=text))
    # Budget includes the serialized data and provenance, not just raw text.
    if len(json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_BYTES:
        raise ValueError("collaboration context byte budget exceeded")
    return items


def grant_context(conn, run_id, step_key, *, actor_id, member_id, refs, now):
    """Freeze an exact owner-approved manifest before the recipient claims.

    refs: list of (owner_message | dependency_result, positive integer ID).
    Root owner message and every direct prerequisite result are mandatory.
    Owner messages are same-room, un-deleted and authored by the real owner.
    """
    _require_write(conn)
    _owner(conn, actor_id)
    row = _step(conn, run_id, step_key, now)
    if row[5] != "pending":
        raise ValueError("collaboration context grant requires pending step")
    provider = _recipient(conn, member_id, row[4])
    if executor_binding(conn,member_id) is not None:
        binding = require_executor(conn,member_id)
        if binding[0] != actor_id:
            raise ValueError("executor owner mismatch")
    if (not isinstance(refs, (list, tuple)) or not 1 <= len(refs) <= MAX_ITEMS
            or any(not isinstance(x, (list, tuple)) or len(x) != 2
                   or x[0] not in ("owner_message","dependency_result")
                   or type(x[1]) is not int or x[1] <= 0 for x in refs)):
        raise ValueError("invalid collaboration context references")
    refs = [tuple(x) for x in refs]
    if len(set(refs)) != len(refs) or ("owner_message", row[1]) not in refs:
        raise ValueError("duplicate references or missing owner request")
    if {i for kind,i in refs if kind == "dependency_result"} != _required_results(conn,run_id,step_key):
        raise ValueError("context must match direct approved dependencies")
    items = _resolve(conn, run_id, step_key, row, actor_id, refs)
    manifest = json.dumps([[i["kind"],i["source_id"],i["sha256"]] for i in items],
                          separators=(",", ":"))
    existing = conn.execute(
        "SELECT owner_id,member_id,provider,manifest,revoked FROM collaboration_context_grants "
        "WHERE run_id=? AND step_key=?", (run_id, step_key)).fetchone()
    if existing:
        if tuple(existing) != (actor_id, member_id, provider, manifest, 0):
            raise ValueError("collaboration context grant is immutable or revoked")
        return False
    conn.execute(
        "INSERT INTO collaboration_context_grants(run_id,step_key,owner_id,member_id,provider,manifest)"
        " VALUES(?,?,?,?,?,?)", (run_id,step_key,actor_id,member_id,provider,manifest))
    return True


def revoke_context(conn, run_id, step_key, *, actor_id):
    _require_write(conn)
    _owner(conn, actor_id)
    return bool(conn.execute(
        "UPDATE collaboration_context_grants SET revoked=1 WHERE run_id=? AND step_key=? "
        "AND owner_id=? AND revoked=0", (run_id,step_key,actor_id)).rowcount)


def read_context(conn, task_id, *, member_id, now):
    """Called only after Hub authenticates the current task holder.

    Revalidate the owner, active lease/run, identity, provider and every source.
    No automatic regrant, truncation or fallback to room/private memory.
    """
    _require_write(conn)
    link = conn.execute(
        "SELECT run_id,step_key,member_id FROM collaboration_task_links WHERE task_id=?",
        (task_id,)).fetchone()
    if not link or link[2] != member_id:
        raise ValueError("collaboration context is not assigned to this member")
    run_id, key, _ = link
    row = _step(conn, run_id, key, now)
    provider = _recipient(conn, member_id, row[4])
    task = conn.execute(
        "SELECT room_id,assignee,status,lease_expires FROM tasks WHERE id=?", (task_id,)
    ).fetchone()
    if (row[5] != "running" or not task or task[0] != row[0] or task[1] != member_id
            or task[2] not in ("claimed","working") or task[3] is None or task[3] <= now):
        raise ValueError("collaboration context requires a live task claim")
    return _validated_grant(conn,run_id,key,row,member_id,provider)


def _validated_grant(conn,run_id,key,row,member_id,provider):
    grant = conn.execute(
        "SELECT owner_id,member_id,provider,manifest,revoked FROM collaboration_context_grants "
        "WHERE run_id=? AND step_key=?", (run_id,key)).fetchone()
    if not grant or grant[1] != member_id or grant[2] != provider or grant[4]:
        raise ValueError("collaboration context grant missing, changed or revoked")
    _owner(conn,grant[0])
    binding = executor_binding(conn,member_id)
    if binding is not None:
        binding = require_executor(conn,member_id)
        if binding[0] != grant[0]:
            raise ValueError("executor owner mismatch")
    manifest = json.loads(grant[3])
    items = _resolve(conn,run_id,key,row,grant[0],[(kind,i) for kind,i,_ in manifest])
    if [i["sha256"] for i in items] != [d for _,_,d in manifest]:
        raise ValueError("collaboration context source changed")
    result = {"run_id":run_id,"step_key":key,"provider":provider,
              "items":items,"max_bytes":MAX_BYTES,"deadline":row[3]}
    if binding is not None:
        result.update(access_kind=binding[2],account_ref=binding[3])
    return result


def validate_executor_claim(conn, task_id, *, member_id, now):
    """Preflight a dedicated executor claim before reserving its budget."""
    _require_write(conn)
    binding = require_executor(conn,member_id)
    link = conn.execute(
        "SELECT run_id,step_key,member_id FROM collaboration_task_links WHERE task_id=?",
        (task_id,)).fetchone()
    if not link or link[2] != member_id:
        raise ValueError("collaboration executor may only claim its linked tasks")
    run_id,key,_ = link
    row = _step(conn,run_id,key,now)
    if row[5] != "pending" or row[4] != str(member_id):
        raise ValueError("collaboration step is not pending for this executor")
    _validated_grant(conn,run_id,key,row,member_id,binding[1])
