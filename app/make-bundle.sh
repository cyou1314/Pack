#!/bin/bash
# Build the Pack app and wrap it in a minimal .app bundle (ad-hoc signed).
# A bare SwiftPM executable launched from a non-GUI shell may fail to reach
# the window server; a proper bundle + `open` is the reliable route.
# If Assets/AppIcon-1024.png exists, an AppIcon.icns is generated into the
# bundle (owner-supplied artwork, cropped to a rounded-square master).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$SCRIPT_DIR"

if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [new-output.app]" >&2
  exit 2
fi

if [ "$#" -eq 0 ]; then
  # Preserve the established developer workflow: rebuild only this known,
  # repository-local bundle when no custom output path is supplied.
  APP="$SCRIPT_DIR/Pack.app"
  REPLACE_EXISTING=1
else
  APP_INPUT="$1"
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
      echo "Output path must end in .app." >&2
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
  REPLACE_EXISTING=0
fi

swift build
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pack-app-bundle.XXXXXX")"
TMP_APP="$TMP_ROOT/Pack.app"
trap 'rm -rf "$TMP_ROOT"' EXIT
mkdir -p "$TMP_APP/Contents/MacOS" "$TMP_APP/Contents/Resources"
cp .build/debug/PackApp "$TMP_APP/Contents/MacOS/PackApp"
cp Info.plist "$TMP_APP/Contents/Info.plist"
mkdir -p "$TMP_APP/Contents/Resources/avatars"
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
codesign --force --sign - "$TMP_APP" 2>/dev/null || true

if [ "$REPLACE_EXISTING" -eq 1 ]; then
  rm -rf "$APP"
fi
# -n prevents a concurrently-created explicit destination from being
# overwritten (or treated as a directory to move the bundle into).
mv -n "$TMP_APP" "$APP"
if [ -d "$TMP_APP" ]; then
  echo "Output appeared during build; refusing to overwrite: $APP" >&2
  exit 1
fi

echo "built: $APP"
echo "launch with: open $APP"
