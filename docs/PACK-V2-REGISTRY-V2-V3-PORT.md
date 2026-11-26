# Foundation v2/v3 port and Provider catalog reconciliation

Offline integration only. Source: Foundation ae45ef3da81f457c3ab54ea7a3bcabae451d98ef.
The v2/v3 definitions and strict task reference helpers are copied without
changing their checksum inputs. The v1 checksum is cross-checked with the
previous port. The current main Hub already creates the audit, approval and
queue indexes; the port verifies their definitions before accepting existing
same-name indexes. It preserves existing non-null historical associations
even when main and Foundation parsing rules differ. No live parser is changed.

Reserved Hub ledger versions:
- 1 Registry; 2 audit/approval; 3 task hotpath indexes.
- 4 Inbox; 5 Gateway credentials; 6 consumer leases; 7 Session projection.
- Catalog correction must use a new version after the whole reserved chain
  is audited (8 is a candidate, not assigned here). No ledger gaps.

Provider reconciliation target: gemini-api -> gemini, volcengine ->
volcengine (distinct from doubao), native/API mode remains separate.
Only repair an unclassified v1 projection after validating the original
member label and absence of conflicting bindings; never convert an existing
active connection based on a label. Do not alter v1 or invent live login state.
This catalog correction is a design boundary, not implemented in this slice.

No production migration runner, backups or activation. Do not run against a
real Hub DB. Test: python hub/test_registry_v2_v3_port.py.
Next: port v4 Inbox and v5 Gateway schema and their tests before catalog repair.
