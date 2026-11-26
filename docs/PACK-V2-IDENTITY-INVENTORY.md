# Collaboration identity inventory: first integration slice

This offline, read-only diagnostic compares the opt-in collaboration executor
ledger on PR #52 with the Foundation Agent Registry schema. It is deliberately
not a migration, a live Hub endpoint, or a new source of identity.

- Map ChatGPT native and OpenAI API to the OpenAI provider, with distinct modes;
  similarly map Gemini and Gemini API. Keep Ark (volcengine) distinct from
  Doubao until a reviewed provider-catalog migration establishes its entry.
- Use legacy member ID only to locate a candidate Agent. Do not assume a
  Provider connection belongs to a local profile simply because the mode
  matches. The future binding must be explicit, authenticated, unique, and
  revocable, and must preserve owner and existing room permission checks.
- An active duplicate local profile UUID, a missing member/owner, a missing
  or ambiguous Agent/Connection, or incompatible provider/mode is reported.
- The report never emits account references, credential references, session
  IDs, messages or secrets. Even an empty issue list is not authorization.
- Existing revoked executors are excluded from the active inventory. Muse
  Legacy remains untouched; no changes to its Engine, credentials or task path.
- Run only on a disposable, consistent database copy under a caller-owned read
  transaction. The current main-based schema does not yet include the
  Foundation tables; missing schema is expected and reported without writes.
- This does not replace the Foundation migration checksum, integrity and
  shadow-read checks. The fixtures intentionally exercise only the structural
  subset, not a combined-schema migration.

Validation command: `python hub/test_collaboration_identity_inventory.py`.
The Hub CI job runs the same offline tests; existing CI jobs remain in place.

Next slice: review the actual Foundation backfill and main member provider
labels, define the explicit connection binding with additive migration and
revocation semantics, then prove the combined schema on synthetic databases.
Do not activate production reads or writes based on this diagnostic.
