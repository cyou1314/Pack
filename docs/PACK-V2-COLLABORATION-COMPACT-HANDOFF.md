# Owner-approved compact handoff (optional extension)

A managed collaboration result is retained in tasks.result. For an
intermediate step, the owner may explicitly supply a smaller exact
handoff_text in collaboration_review or a subset of handoffs in
collaboration_review_batch. The existing inspected review version is
required; no automatic summarizer, edits to the original or model calls.
The compact text is owner-written/approved, not necessarily a faithful
summary. The wire representation adds a fixed provenance label. Its
encoded JSON string size must be strictly smaller than the original.

The optional table is installed only by a trusted stopped-copy
migration helper after backup. Separate checksum ledger, frozen v1
schema untouched, no backfill, no startup/CLI production migration.
Existing databases without the extension continue full-result grants;
requesting a compact handoff fails closed. The original and compact
representation are stored separately in the existing Hub database.
No new file, token, network service or provider dependency.

Approval, step reconciliation, compact insert and any newly ready
dispatch are in the same transaction. The insert pins the exact
post-review original digest, owner, task ID, text and approval time.
Only existing direct dependent steps that have not received a grant or
task can use it. At grant/read, verify normal original provenance then
derive a separate compact digest binding original and actual wire text.
The unchanged manifest format pins that digest. Changed/deleted compact
content or changed original invalidates an existing grant, never silently
falls back. The same compact text is used by all direct dependents.
The final result and publication still use the original final answer.

Owner result inspection exposes the original separately from compact
readback; projection uses compact text for approved dependencies.
Batch overflow still rolls back every approval/insert. Single compact
review rolls back if the next step cannot dispatch in that transaction;
a later independent review may still encounter overflow. No guarantee
every future fan-in fits. No editing a compact handoff after approval.

Tests: real MCP compare run with oversized original, invalid handoff
rollback, outsider rejection, compact batch approval, original readback
and compact final context; migration ledger compatibility/tamper check.
This revision adds the Hub contract, not Mac text editing/preview UI,
production migration orchestration or live Provider verification.
Next: test post-grant source tampering, add exact owner draft preview
and Mac review UI, then explicit backup/rehearsal activation workflow.
