# Collaboration efficiency: route quality and workload preview

Existing Focus/Compare/Team DAGs remain unchanged. Focus has one call;
Compare analyzes with leader plus 1–2 peers, then synthesizes; Team executes
1–3 workers, then independent review, then synthesis. No free-running
Agent-to-Agent conversation or implicit extra rounds. The App now shows
the first wave task count and serial dependency stages, not estimated
duration or measured parallelism. The optional exact-budget action sets
max_calls to planned calls without modifying the route policy.

Routing stays deterministic and owner-confirmed. Within the same quota
readiness tier, prefer a provider ecosystem not already selected; ties
use reported quota percentage then member ID. ChatGPT/OpenAI,
Gemini/Gemini API, Doubao/Ark are grouped for this heuristic. Known quota
still precedes unknown, and unknown still requires opt-in. Same-family
fallback is allowed when necessary; neither ecosystem diversity nor
quota proves model quality, independent reasoning or equal cost.
Case-insensitive duplicate account references never fill multiple roles.

Preview uses a consistent read transaction and checks the existing
Registry execution gate; the new execution_allowed field excludes
paused/disabled or otherwise unauthorized bound accounts. Managed
new-run creation rechecks the same gate before source/task insertion
inside its existing write transaction. No new authorization bypass,
migration or default enable. Existing identical create replay remains
read-only; legacy unbound compatibility with opt-in off remains.
A preview is not a reservation. Actual claim/context/run gates still
apply, and changing account state after creation can leave tasks
unavailable until operator reconciliation.

Tests cover deterministic diversity, quota precedence, duplicate
references, exclusion and structural wave counts, plus the real MCP
preview/create rejection of a disabled or paused Registry account.
No live model use, timing or quality benchmark. Next: bound dependency
context fan-in, then design explicit batch review/approval to reduce
human interruptions without broadening context grants.
