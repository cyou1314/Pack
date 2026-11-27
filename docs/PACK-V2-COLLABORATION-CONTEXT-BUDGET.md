# Collaboration context fan-in budgeting

Existing grants serialize root owner request and every direct approved
dependency into a 16 KiB JSON array. Individually acceptable results
can exceed the combined budget and stop a run on dispatch. This slice
adds read-only projections to the owner workspace and result inspection.
Uses the same compact UTF-8 JSON serialization as grant_context, with
actual known text and IDs, fixed-length SHA placeholders, empty text
for missing results, and a one-digit placeholder for unknown task IDs.
When all dependencies have results and actual IDs, the measured byte
count matches the actual grant (assuming source versions unchanged).
Pending and unapproved results are explicitly counted; projections do
not authorize or replace them. The equal-share suggestion is available
encoded text growth over empty strings, not raw UTF-8 bytes, tokens or
a hard per-result limit. Unknown future task IDs may consume more bytes.
Only direct downstream pending steps are shown in result inspection.
No text, hashes, secrets or new external requests in these fields.
No data mutation, schema change, automatic summarization, truncation
or changed review semantics. A too-large actual context still stops
dispatch as before; this warning does not solve overflow by itself.
Next: design an explicit owner-reviewed compact handoff representation
that preserves original results and versions, then batch review. Do
not silently shorten, overwrite original results or add model calls.
Tests cover Unicode/escaping, missing and provisional results, overflow
and exact parity with an actual grant; MCP integration checks owner
workspace and inspection. CI compilation of Swift UI is pending.
