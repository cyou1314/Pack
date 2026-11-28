#!/bin/bash
# Explicit developer-only bundle. Never used by the production DMG builder.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
[ "$#" -eq 3 ] || { echo "usage: make-browser-dev-bundle.sh NEW_APP CANDIDATE_DIRECTORY CANDIDATE_MANIFEST" >&2; exit 2; }
OUT="$1"; PAYLOAD="$2"; LOCK="$3"
case "$OUT" in /*.app) ;; *) echo "absolute new .app path required" >&2; exit 2;; esac
[ -d "$(dirname "$OUT")" ] && [ ! -e "$OUT" ] && [ ! -L "$OUT" ] || exit 1
REV="$(git -C "$ROOT" rev-parse HEAD)"
ARCH="$(uname -m)"
python3 "$ROOT/installer/browser-payload.py" verify --payload "$PAYLOAD" --lock "$LOCK" --revision "$REV" --architecture "$ARCH"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/pack-native-dev.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
APP="$TMP/Pack Native Development.app"
bash "$ROOT/app/make-bundle.sh" "$APP"
RES="$APP/Contents/Resources/NativeBrowserCandidate"
ditto "$PAYLOAD" "$RES"
python3 "$ROOT/installer/browser-payload.py" verify --payload "$RES" --lock "$LOCK" --revision "$REV" --architecture "$ARCH"
/usr/libexec/PlistBuddy -c 'Add :PackNativeBrowserDevelopment bool true' "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Set :CFBundleDisplayName Pack Native Development' "$APP/Contents/Info.plist"
codesign --force --sign - "$APP"
codesign --verify --deep --strict "$APP"
python3 "$ROOT/installer/browser-payload.py" verify --payload "$RES" --lock "$LOCK" --revision "$REV" --architecture "$ARCH"
# Reserve only a new destination; never replace Pack.app.
mkdir "$OUT"
mv "$APP/Contents" "$OUT/Contents"
echo "Developer-only candidate bundle: $OUT"
echo "Not approved for production install. Quit other Pack instances before opening."
