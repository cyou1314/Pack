"""Hub-wide capacity for explicitly bound Registry task claims.

Use existing durable task rows and binding records. Caller owns the same
BEGIN IMMEDIATE transaction as task_claim. Never expire an active claim
by time alone; the existing lifecycle must actually transition the task.
This is Hub claim capacity, not a count of remote Provider requests.
"""
MAX_ACTIVE_CLAIMS=4


def admission_refusal(conn, member_id):
    if not conn.in_transaction:
        raise RuntimeError("capacity check requires claim transaction")
    if type(member_id) is not int or member_id<=0:
        raise ValueError("invalid member")
    # Count all bound active claims, including paused/revoked identities.
    # A cancelled/reviewed task no longer owns a Hub slot, but the account
    # lease must remain held if the remote submission is ambiguous.
    total, own=conn.execute("""
        SELECT COUNT(*), COALESCE(SUM(CASE WHEN t.assignee=? THEN 1 ELSE 0 END),0)
        FROM tasks t
        JOIN collaboration_task_links l ON l.task_id=t.id
        JOIN collaboration_connection_bindings b ON b.member_id=l.member_id
        WHERE t.status IN ('claimed','working')
        """,(member_id,)).fetchone()
    if own:
        return "execution_account_busy"
    if total>=MAX_ACTIVE_CLAIMS:
        return "execution_capacity"
    return None
