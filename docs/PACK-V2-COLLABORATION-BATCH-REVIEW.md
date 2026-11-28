# Atomic parallel wave review

Owner-only collaboration_review_batch approves 2–3 outstanding parallel
worker/independent-analysis steps in one managed run, after every
outstanding member of that wave is in review. Previously individually
approved members may remain done. A complete inspected version map is
required; no final/synthesis/reviewer step, implicit approve, reject-all,
or credential change. All versions are checked before any mutation.
The existing review transition is reused, with strict downstream
dispatch for batch only. A downstream grant failure rolls back the
whole batch rather than committing approval then stopping the run.
No schema changes and no changed behavior for existing single review.
Unknown responses must be reconciled; no automatic replay.

Mac workspace loads each selected result through the existing scoped
read endpoint, shows all originals, requires explicit batch consent
and rejects known projected overflow. Server remains authoritative;
version drift and excessive actual serialized context are rejected.
The private text remains transient. No extra model calls, silent
summarization, output edits or publication. A known projection may
conservatively prevent a batch even if a later change would fit.
The 16 KiB limit remains. Single review can still stop an overflowing
run under existing semantics. A compact owner-approved handoff needs
a separate versioned representation and migration design; not implemented.

MCP integration covers outsider denial, incomplete batch, stale version,
rollback on oversized combined context, exact successful batch and
replay refusal, plus the existing full final synthesis/publication.
Swift UI compile and tests via CI; no real Provider or Mac runtime
acceptance yet.
