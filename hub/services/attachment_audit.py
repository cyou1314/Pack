"""Read-only Pack attachment inventory; no deletion or database writes.

Run as: python -m services.attachment_audit --db PATH --attachments PATH
Findings are candidates for review, never automatic cleanup instructions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import quote

EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SHARD_RE = re.compile(r"^[0-9a-f]{2}$")
BLOB_RE = re.compile(r"^([0-9a-f]{64})\.(png|jpg|webp|gif)$")
MAX_FILE_BYTES = 10 * 1024 * 1024


def audit_attachments(db_path, attachment_dir, *, grace_seconds=86400,
                      now=None, sample_limit=40):
    """Compare a consistent read-only SQLite snapshot with blob storage."""
    if grace_seconds < 0 or sample_limit < 0:
        raise ValueError("grace_seconds and sample_limit must be non-negative")
    clock = time.time() if now is None else float(now)
    root = Path(os.path.abspath(os.fspath(attachment_dir)))
    if not root.is_dir() or root.is_symlink():
        raise ValueError("attachment directory missing or unsafe")
    db = os.path.abspath(os.fspath(db_path))
    counts = {
        "db_rows": 0,
        "referenced_blobs": 0,
        "missing": 0,
        "size_mismatch": 0,
        "hash_mismatch": 0,
        "unsafe_path": 0,
        "unreadable": 0,
        "invalid_records": 0,
        "metadata_conflict": 0,
        "stale_orphan_candidates": 0,
        "recent_unreferenced": 0,
        "stale_temp_candidates": 0,
        "recent_temp": 0,
        "unexpected_entries": 0,
    }
    samples = []

    def finding(kind, path, detail=""):
        counts[kind] += 1
        if len(samples) < sample_limit:
            samples.append({"category": kind, "path": path, "detail": detail})

    uri = "file:" + quote(db, safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        rows = conn.execute(
            "SELECT id,sha256,mime,size FROM attachments ORDER BY id"
        ).fetchall()
        conn.rollback()
    finally:
        conn.close()
    counts["db_rows"] = len(rows)
    referenced = {}
    for record in rows:
        digest, mime, size = record["sha256"], record["mime"], record["size"]
        if (not isinstance(digest, str) or not SHA_RE.fullmatch(digest) or
                mime not in EXTENSIONS or not isinstance(size, int) or
                not 0 < size <= MAX_FILE_BYTES):
            finding("invalid_records", f"attachment:{record['id']}",
                    "invalid digest, MIME or stored size")
            continue
        rel = f"{digest[:2]}/{digest}.{EXTENSIONS[mime]}"
        if rel in referenced:
            if referenced[rel]["size"] != size:
                finding("metadata_conflict", rel, "inconsistent recorded sizes")
        else:
            referenced[rel] = {"size": size, "sha": digest}
    counts["referenced_blobs"] = len(referenced)

    canonical_root = os.path.realpath(root)
    for rel, info in referenced.items():
        path = root / rel
        if os.path.islink(path.parent) or os.path.islink(path):
            finding("unsafe_path", rel, "symlink in content path")
            continue
        if os.path.commonpath((canonical_root, os.path.realpath(path))) != canonical_root:
            finding("unsafe_path", rel, "content path escapes root")
            continue
        try:
            file_stat = path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            finding("missing", rel, "DB references a missing blob")
            continue
        except OSError:
            finding("unreadable", rel, "cannot stat blob")
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            finding("unsafe_path", rel, "referenced blob is not regular")
            continue
        if file_stat.st_size != info["size"]:
            finding("size_mismatch", rel, "recorded size differs from file")
            continue
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    finding("unsafe_path", rel, "blob changed type during open")
                    continue
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != info["sha"]:
                finding("hash_mismatch", rel, "file digest differs from DB")
        except OSError:
            finding("unreadable", rel, "cannot read blob")

    def classify_unreferenced(relative, entry, temp=False):
        try:
            mtime = entry.stat(follow_symlinks=False).st_mtime
        except OSError:
            finding("unreadable", relative, "cannot stat unreferenced file")
            return
        if clock - mtime >= grace_seconds:
            kind = "stale_temp_candidates" if temp else "stale_orphan_candidates"
        else:
            kind = "recent_temp" if temp else "recent_unreferenced"
        finding(kind, relative, "report only; never delete automatically")

    with os.scandir(root) as shards:
        for shard in shards:
            if shard.is_symlink():
                finding("unsafe_path", shard.name, "symlinked root entry")
                continue
            if not shard.is_dir(follow_symlinks=False) or not SHARD_RE.fullmatch(shard.name):
                finding("unexpected_entries", shard.name, "unknown storage entry")
                continue
            with os.scandir(shard.path) as entries:
                for entry in entries:
                    rel = f"{shard.name}/{entry.name}"
                    if rel in referenced:
                        continue
                    if entry.is_symlink():
                        finding("unsafe_path", rel, "symlinked file")
                    elif not entry.is_file(follow_symlinks=False):
                        finding("unexpected_entries", rel, "non-regular entry")
                    elif entry.name.startswith(".pack-upload-") or ".tmp-" in entry.name:
                        classify_unreferenced(rel, entry, temp=True)
                    else:
                        match = BLOB_RE.fullmatch(entry.name)
                        if match and match.group(1)[:2] == shard.name:
                            classify_unreferenced(rel, entry)
                        else:
                            finding("unexpected_entries", rel, "unrecognized blob name")

    blocking = (
        "missing", "size_mismatch", "hash_mismatch", "unsafe_path", "unreadable",
        "invalid_records", "metadata_conflict", "stale_orphan_candidates",
        "stale_temp_candidates", "unexpected_entries",
    )
    return {
        "ok": all(counts[key] == 0 for key in blocking),
        "read_only": True,
        "grace_seconds": grace_seconds,
        "counts": counts,
        "samples": samples,
        "note": "No files deleted. Stale candidates require manual review and backup reconciliation.",
    }


def main(argv=None):
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Read-only Pack attachment consistency audit")
    parser.add_argument("--db", default=str(default_root / "pack.db"))
    parser.add_argument("--attachments", default=str(default_root / "attachments"))
    parser.add_argument("--grace-hours", type=float, default=24.0)
    parser.add_argument("--sample-limit", type=int, default=40)
    parser.add_argument("--strict", action="store_true",
                        help="exit nonzero on findings; never remove anything")
    args = parser.parse_args(argv)
    try:
        report = audit_attachments(
            args.db, args.attachments,
            grace_seconds=args.grace_hours * 3600,
            sample_limit=args.sample_limit,
        )
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(2, f"attachment audit failed: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if args.strict and not report["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
