#!/bin/bash
# Regression tests for make-bundle.sh path safety and no-clobber publication.
# All negative cases must fail before invoking a real Swift toolchain.
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
grep -Fq "Output already exists" "$TMP/existing.log" || {
  echo "FAIL: existing output was not rejected before the build" >&2
  cat "$TMP/existing.log" >&2
  exit 1
}
[ "$(cat "$TARGET/sentinel.txt")" = "preserve-me" ] || {
  echo "FAIL: existing output content was modified" >&2
  exit 1
}

RELATIVE="$TMP/relative.app"
mkdir -p "$RELATIVE"
printf 'relative-preserve\n' > "$RELATIVE/sentinel.txt"
(
  cd "$TMP"
  if bash "$ROOT/make-bundle.sh" "relative.app" >"$TMP/relative.log" 2>&1; then
    echo "FAIL: existing relative output path was accepted" >&2
    exit 1
  fi
)
grep -Fq "Output already exists" "$TMP/relative.log" || {
  echo "FAIL: relative output was not resolved against the caller directory" >&2
  cat "$TMP/relative.log" >&2
  exit 1
}
[ "$(cat "$RELATIVE/sentinel.txt")" = "relative-preserve" ] || {
  echo "FAIL: relative output content was modified" >&2
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

# Reproduce a destination directory appearing during Swift build. The build
# and codesign commands are stubbed; the test verifies publication refuses the
# path and leaves the concurrently-created directory untouched.
FIXTURE="$TMP/fixture-app"
STUBS="$TMP/stubs"
RACE_TARGET="$TMP/race.app"
mkdir -p "$FIXTURE/.build/debug" "$FIXTURE/ui-mock/avatars" "$STUBS"
cp "$ROOT/make-bundle.sh" "$FIXTURE/make-bundle.sh"
cp "$ROOT/Info.plist" "$FIXTURE/Info.plist"
cp "$ROOT/ui-mock/avatars/dispatcher.webp" "$ROOT/ui-mock/avatars/absent.webp" "$FIXTURE/ui-mock/avatars/"
cp "$ROOT/ui-mock/mark.png" "$FIXTURE/ui-mock/mark.png"
printf '#!/bin/sh\nexit 0\n' > "$FIXTURE/.build/debug/PackApp"
chmod +x "$FIXTURE/.build/debug/PackApp"
cat > "$STUBS/swift" <<'SH'
#!/bin/bash
mkdir -p "$PACK_TEST_RACE_TARGET"
printf 'created-during-build\n' > "$PACK_TEST_RACE_TARGET/sentinel.txt"
SH
cat > "$STUBS/codesign" <<'SH'
#!/bin/sh
exit 0
SH
chmod +x "$STUBS/swift" "$STUBS/codesign"
if PATH="$STUBS:$PATH" PACK_TEST_RACE_TARGET="$RACE_TARGET" \
  bash "$FIXTURE/make-bundle.sh" "$RACE_TARGET" >"$TMP/race.log" 2>&1; then
  echo "FAIL: concurrently-created output path was accepted" >&2
  exit 1
fi
grep -Fq "Output path appeared during build" "$TMP/race.log" || {
  echo "FAIL: publication did not report the output-path race" >&2
  cat "$TMP/race.log" >&2
  exit 1
}
[ "$(cat "$RACE_TARGET/sentinel.txt")" = "created-during-build" ] || {
  echo "FAIL: concurrently-created destination was modified" >&2
  exit 1
}
[ ! -e "$RACE_TARGET/Pack.app" ] || {
  echo "FAIL: bundle was moved inside the concurrently-created destination" >&2
  exit 1
}

echo "PASS make-bundle output path safety"
