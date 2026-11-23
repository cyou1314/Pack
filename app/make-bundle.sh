#!/bin/bash
# Build Pack.app at an explicit output path (ad-hoc signed).
# A bare SwiftPM executable launched from a non-GUI shell may fail to reach
# the window server; use open on the resulting bundle for GUI launch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
APP="${1:-$ROOT/Pack.app}"
[[ "$APP" == /* ]] || APP="$ROOT/$APP"
[[ "$APP" == *.app ]] || { echo "Output must end in .app: $APP" >&2; exit 1; }
[[ ! -e "$APP" && ! -L "$APP" ]] || { echo "Output exists; choose a new path: $APP" >&2; exit 1; }
CONFIGURATION="${PACK_BUILD_CONFIGURATION:-debug}"
case "$CONFIGURATION" in
  debug|release) ;;
  *) echo "PACK_BUILD_CONFIGURATION must be debug or release." >&2; exit 1 ;;
esac

swift build -c "$CONFIGURATION"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/avatars"
cp ".build/$CONFIGURATION/PackApp" "$APP/Contents/MacOS/PackApp"
cp Info.plist "$APP/Contents/Info.plist"
cp ui-mock/avatars/dispatcher.webp ui-mock/avatars/absent.webp "$APP/Contents/Resources/avatars/"
cp ui-mock/mark.png "$APP/Contents/Resources/mark.png"

ICON_TMP=""
cleanup() {
  if [[ -n "$ICON_TMP" ]]; then rm -rf "$ICON_TMP"; fi
}
trap cleanup EXIT
if [ -f Assets/AppIcon-1024.png ]; then
  ICON_TMP="$(mktemp -d "${TMPDIR:-/tmp}/pack-icon.XXXXXX")"
  ICONSET="$ICON_TMP/AppIcon.iconset"
  mkdir -p "$ICONSET"
  for spec in "16:16x16" "32:16x16@2x" "32:32x32" "64:32x32@2x" "128:128x128" "256:128x128@2x" "256:256x256" "512:256x256@2x" "512:512x512" "1024:512x512@2x"; do
    px="${spec%%:*}"; name="${spec##*:}"
    sips -z "$px" "$px" Assets/AppIcon-1024.png --out "$ICONSET/icon_${name}.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
  echo "icon: AppIcon.icns generated"
fi

plutil -lint "$APP/Contents/Info.plist" >/dev/null
codesign --force --sign - "$APP" >/dev/null
codesign --verify --deep --strict "$APP"
echo "built: $APP"
echo "launch with: open $APP"
