#!/bin/bash
# Build the Pack app and wrap it in a minimal .app bundle (ad-hoc signed).
# A bare SwiftPM executable launched from a non-GUI shell may fail to reach
# the window server; a proper bundle + `open` is the reliable route.
# If Assets/AppIcon-1024.png exists, an AppIcon.icns is generated into the
# bundle (owner-supplied artwork, cropped to a rounded-square master).
set -euo pipefail
cd "$(dirname "$0")"
swift build
APP=Pack.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp .build/debug/PackApp "$APP/Contents/MacOS/PackApp"
cp Info.plist "$APP/Contents/Info.plist"
if [ -f Assets/AppIcon-1024.png ]; then
  ICONSET=$(mktemp -d)/AppIcon.iconset
  mkdir -p "$ICONSET"
  for spec in "16:16x16" "32:16x16@2x" "32:32x32" "64:32x32@2x" "128:128x128" "256:128x128@2x" "256:256x256" "512:256x256@2x" "512:512x512" "1024:512x512@2x"; do
    px="${spec%%:*}"; name="${spec##*:}"
    sips -z "$px" "$px" Assets/AppIcon-1024.png --out "$ICONSET/icon_${name}.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
  echo "icon: AppIcon.icns generated"
fi
codesign --force --sign - "$APP" 2>/dev/null || true
echo "built: $APP"
echo "launch with: open $APP"
