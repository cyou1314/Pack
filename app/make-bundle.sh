#!/bin/bash
# Build Pack.app at a new output path and publish it without clobbering an
# existing destination. Supports debug/release and verifies the signed bundle.
set -euo pipefail
CALLER_DIR="$(pwd -P)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"

if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [new-output.app]" >&2
  exit 2
fi

APP_INPUT="${1:-$SCRIPT_DIR/Pack.app}"
[ -n "$APP_INPUT" ] || {
  echo "Invalid app bundle output path." >&2
  exit 2
}
case "$APP_INPUT" in
  /*) ;;
  *) APP_INPUT="$CALLER_DIR/$APP_INPUT" ;;
esac
while [ "$APP_INPUT" != "/" ] && [ "${APP_INPUT%/}" != "$APP_INPUT" ]; do
  APP_INPUT="${APP_INPUT%/}"
done
APP_NAME="$(basename "$APP_INPUT")"
APP_PARENT="$(dirname "$APP_INPUT")"
case "$APP_NAME" in
  ""|"."|".."|"/")
    echo "Invalid app bundle output path." >&2
    exit 2
    ;;
esac
case "$APP_NAME" in
  *.app) ;;
  *)
    echo "Output path must end in .app: $APP_INPUT" >&2
    exit 2
    ;;
esac
[ -d "$APP_PARENT" ] || {
  echo "Output parent directory does not exist: $APP_PARENT" >&2
  exit 2
}
APP_PARENT="$(cd "$APP_PARENT" && pwd -P)"
APP="$APP_PARENT/$APP_NAME"
if [ -e "$APP" ] || [ -L "$APP" ]; then
  echo "Output already exists; choose a new path: $APP" >&2
  exit 1
fi

CONFIGURATION="${PACK_BUILD_CONFIGURATION:-debug}"
case "$CONFIGURATION" in
  debug|release) ;;
  *) echo "PACK_BUILD_CONFIGURATION must be debug or release." >&2; exit 1 ;;
esac

cd "$SCRIPT_DIR"
swift build -c "$CONFIGURATION"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pack-app-bundle.XXXXXX")"
TMP_APP="$TMP_ROOT/Pack.app"
RESERVED_APP=0
cleanup() {
  local rc=$?
  trap - EXIT
  if [ "$RESERVED_APP" -eq 1 ]; then
    rm -rf "$APP" || true
  fi
  rm -rf "$TMP_ROOT" || true
  exit "$rc"
}
trap cleanup EXIT
mkdir -p "$TMP_APP/Contents/MacOS" "$TMP_APP/Contents/Resources/avatars"
cp ".build/$CONFIGURATION/PackApp" "$TMP_APP/Contents/MacOS/PackApp"
cp Info.plist "$TMP_APP/Contents/Info.plist"
cp ui-mock/avatars/dispatcher.webp ui-mock/avatars/absent.webp "$TMP_APP/Contents/Resources/avatars/"
cp ui-mock/mark.png "$TMP_APP/Contents/Resources/mark.png"

if [ -f Assets/AppIcon-1024.png ]; then
  ICONSET="$TMP_ROOT/AppIcon.iconset"
  mkdir -p "$ICONSET"
  for spec in "16:16x16" "32:16x16@2x" "32:32x32" "64:32x32@2x" "128:128x128" "256:128x128@2x" "256:256x256" "512:256x256@2x" "512:512x512" "1024:512x512@2x"; do
    px="${spec%%:*}"; name="${spec##*:}"
    sips -z "$px" "$px" Assets/AppIcon-1024.png --out "$ICONSET/icon_${name}.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$TMP_APP/Contents/Resources/AppIcon.icns"
  echo "icon: AppIcon.icns generated"
fi

plutil -lint "$TMP_APP/Contents/Info.plist" >/dev/null
codesign --force --sign - "$TMP_APP" >/dev/null
codesign --verify --deep --strict "$TMP_APP"

# mkdir is an atomic no-clobber reservation. If another process creates the
# destination during Swift build/signing, publication fails without touching it.
if ! mkdir "$APP"; then
  echo "Output path appeared during build; refusing to overwrite: $APP" >&2
  exit 1
fi
RESERVED_APP=1
if [ -e "$APP/Contents" ] || [ -L "$APP/Contents" ]; then
  echo "Output bundle contents path is already occupied: $APP/Contents" >&2
  exit 1
fi
mv "$TMP_APP/Contents" "$APP/Contents"
rmdir "$TMP_APP"
[ -x "$APP/Contents/MacOS/PackApp" ] || {
  echo "App executable missing after bundle publication." >&2
  exit 1
}
[ -f "$APP/Contents/Info.plist" ] || {
  echo "App Info.plist missing after bundle publication." >&2
  exit 1
}
RESERVED_APP=0

echo "built: $APP"
echo "launch with: open $APP"
