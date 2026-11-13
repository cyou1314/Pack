#!/bin/bash
# Build the Pack app and wrap it in a minimal .app bundle (ad-hoc signed).
# A bare SwiftPM executable launched from a non-GUI shell may fail to reach
# the window server; a proper bundle + `open` is the reliable route.
set -euo pipefail
cd "$(dirname "$0")"
swift build
APP=Pack.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp .build/debug/PackApp "$APP/Contents/MacOS/PackApp"
cp Info.plist "$APP/Contents/Info.plist"
codesign --force --sign - "$APP" 2>/dev/null || true
echo "built: $APP"
echo "launch with: open $APP"
