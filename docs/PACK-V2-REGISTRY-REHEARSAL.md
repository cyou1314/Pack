# Registry controlled migration rehearsal

This CLI cannot apply migrations to the source database or activate Runtime.
Use a private existing output directory, and explicitly specify the source:

    python hub/registry_rehearsal.py inspect --database /path/to/pack.db
    python hub/registry_rehearsal.py rehearse --database /path/to/pack.db --output-root /path/to/private-directory

The read-only preflight validates base Hub shape, ledger prefix, schema,
integrity and foreign keys. It is structural only; data conflicts may be
found by the rehearsal. SQLite online backup uses a read transaction to
include committed WAL pages. SQLite may use read-side WAL/SHM files or
locks: this is not a promise of zero filesystem activity. Use a stopped
Hub or verified copy when practicable. The source DB is never opened for
write or replaced by this command.

A new mode-0700 run directory contains a verified mode-0600 source snapshot,
a separate migration rehearsal copy, and a SHA-256 manifest. Failed
migrations retain the snapshot; the manifest reports blocked, never ready.
A failure during snapshot creation itself may leave a partial run directory
without manifest; do not use it. A snapshot is not immutable storage;
protect the directory, and reverify the hash before recovery. No retention
or cleanup is automatic. Output contains private DB content, never upload
or share it casually. Attachments are NOT backed up by this tool; existing
full Hub backup/restore procedures remain required for deployment.

Path/ownership checks assume a trusted user-owned directory and no hostile
same-user concurrent path replacement. Source changes after snapshot are
not included. Production activation still requires stopped services,
fresh full backup, registry shadow consistency and explicit operator
approval. No installer or production startup changes in this slice.
