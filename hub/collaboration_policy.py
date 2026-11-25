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
