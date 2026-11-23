#!/bin/bash
# Regression tests for make-bundle.sh argument safety. These cases must fail
# before Swift is invoked, so they run on both Linux and macOS CI runners.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/pack-bundle-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

TARGET="$TMP/existing.app"
mkdir -p "$TARGET"
printf 'preserve-me\n' > "$TARGET/sentinel.txt"

if bash "$ROOT/make-bundle.sh" "$TARGET" >"$TMP/existing.log" 2>&1; then
  echo "FAIL: existing explicit output path was accepted" >&2
  exit 1
fi
[ "$(cat "$TARGET/sentinel.txt")" = "preserve-me" ] || {
  echo "FAIL: existing output content was modified" >&2
  exit 1
}

if bash "$ROOT/make-bundle.sh" "$TMP/not-an-app" >"$TMP/extension.log" 2>&1; then
  echo "FAIL: output without .app suffix was accepted" >&2
  exit 1
fi

LINK="$TMP/existing-link.app"
ln -s "$TARGET" "$LINK"
if bash "$ROOT/make-bundle.sh" "$LINK" >"$TMP/symlink.log" 2>&1; then
  echo "FAIL: existing symlink output path was accepted" >&2
  exit 1
fi
[ "$(cat "$TARGET/sentinel.txt")" = "preserve-me" ] || {
  echo "FAIL: symlink target content was modified" >&2
  exit 1
}

echo "PASS make-bundle output path safety"
