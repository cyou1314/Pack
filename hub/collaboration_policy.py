"""Pure, opt-in collaboration planning. No network, persistence or execution.

Caller must authenticate the owner, resolve room membership and provider readiness
server-side, and reserve budget atomically before dispatch. Legacy chat is unchanged.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    key: str
    member: str
    role: str
    after: tuple[str, ...] = ()


def plan_collaboration(*, actor_role: str, mode: str, eligible: set[str],
                       leader: str, workers: tuple[str, ...] = (),
                       reviewer: str | None = None, max_calls: int = 5) -> tuple[Step, ...]:
    """Return a bounded DAG or reject the whole plan, never silently broadcast.

    Member identifiers are opaque, room-scoped identities resolved by the caller.
    A planned step is not an authorization to execute tools or share private data.
    """
    if actor_role != "owner":
        raise ValueError("owner authorization required")
    if type(max_calls) is not int or not 1 <= max_calls <= 8:
        raise ValueError("call budget must be 1..8")
    if mode not in ("focus", "compare", "team"):
        raise ValueError("unsupported collaboration mode")
    if not isinstance(workers, tuple) or len(workers) > 3:
        raise ValueError("invalid workers")
    selected = (leader,) + workers + (() if reviewer is None else (reviewer,))
    if any(not isinstance(m, str) or not m or m not in eligible for m in selected):
        raise ValueError("member not eligible")
    if len(set(selected)) != len(selected):
        raise ValueError("roles must be distinct")
    if mode == "focus":
        if workers or reviewer is not None:
            raise ValueError("focus requires only one member")
        steps = (Step("answer", leader, "answer"),)
    elif mode == "compare":
        if not 1 <= len(workers) <= 2 or reviewer is not None:
            raise ValueError("compare requires one or two peers, no reviewer")
        parallel = tuple(Step(f"analysis-{i}", m, "independent-analysis")
                         for i, m in enumerate((leader,) + workers))
        steps = parallel + (Step("final", leader, "synthesis",
                                 tuple(s.key for s in parallel)),)
    else:
        if not workers or reviewer is None:
            raise ValueError("team requires workers and an independent reviewer")
        parallel = tuple(Step(f"work-{i}", m, "worker")
                         for i, m in enumerate(workers))
        review = Step("review", reviewer, "review", tuple(s.key for s in parallel))
        steps = parallel + (review, Step("final", leader, "synthesis",
                                         tuple(s.key for s in parallel) + (review.key,)))
    if len(steps) > max_calls:
        raise ValueError("call budget exceeded")
    return steps


ROLE_OUTPUT_LIMITS = {"answer": 4096, "independent-analysis": 2048,
                      "worker": 3072, "review": 2048, "synthesis": 4096}


def role_for_step(key):
    if key == "answer":
        return "answer"
    if key == "final":
        return "synthesis"
    if key == "review":
        return "review"
    if key in ("analysis-0", "analysis-1", "analysis-2"):
        return "independent-analysis"
    if key in ("work-0", "work-1", "work-2"):
        return "worker"
    raise ValueError("invalid collaboration step")


def suggest_roles(*, mode, candidates, worker_count=1, max_calls=5, allow_unknown_quota=False):
    """Deterministic owner preview; never creates a run or invokes a model."""
    if (type(worker_count) is not int or type(allow_unknown_quota) is not bool
            or type(max_calls) is not int or not 1 <= max_calls <= 8):
        raise ValueError("invalid routing options")
    if mode == "focus":
        needed, calls = 1, 1
    elif mode == "compare" and 1 <= worker_count <= 2:
        needed, calls = 1 + worker_count, 2 + worker_count
    elif mode == "team" and 1 <= worker_count <= 3:
        needed = calls = 2 + worker_count
    else:
        raise ValueError("invalid routing mode or worker count")
    if calls > max_calls:
        raise ValueError("call budget exceeded")
    eligible = [c for c in candidates if c["online"] and c["queue"] == 0
                and not c["active_plan"]
                and (c["quota_state"] == "available" or
                     allow_unknown_quota and c["quota_state"] == "unknown")]
    # Prefer observed headroom; do not pretend to rank model intelligence.
    eligible.sort(key=lambda c: (c["quota_state"] != "available",
                    c["quota_pct"] if c["quota_state"] == "available" else 101,
                    c["member_id"]))
    selected, refs = [], set()
    for c in eligible:
        if c["account_ref"] in refs:
            continue
        refs.add(c["account_ref"])
        selected.append(c)
        if len(selected) == needed:
            break
    if len(selected) != needed:
        return None
    ids = [str(c["member_id"]) for c in selected]
    workers = tuple(ids[1:-1] if mode == "team" else ids[1:])
    reviewer = ids[-1] if mode == "team" else None
    steps = plan_collaboration(actor_role="owner", mode=mode, eligible=set(ids),
                               leader=ids[0], workers=workers, reviewer=reviewer, max_calls=max_calls)
    return {"leader": int(ids[0]), "workers": [int(x) for x in workers],
            "reviewer": int(reviewer) if reviewer else None, "planned_calls": len(steps)}
