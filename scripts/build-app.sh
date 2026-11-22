#!/bin/sh
# Build app/build/Aura.app: compile, bundle with Info.plist and assets, sign ad hoc.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${CONFIG:-release}"

swift build -c "$CONFIG" --package-path "$ROOT/app" --product Aura
BIN="$(swift build -c "$CONFIG" --package-path "$ROOT/app" --show-bin-path)"

APP="$ROOT/app/build/Aura.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN/Aura" "$APP/Contents/MacOS/Aura"
"$ROOT/scripts/build-assets.sh" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Aura</string>
  <key>CFBundleDisplayName</key><string>Aura</string>
  <key>CFBundleIdentifier</key><string>local.aura.Aura</string>
  <key>CFBundleExecutable</key><string>Aura</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.6.1</string>
  <key>CFBundleVersion</key><string>7</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>NSDocumentsFolderUsageDescription</key>
  <string>Aura keeps its vault, the only folder it can work in, inside your Documents folder.</string>
  <key>AuraProjectDir</key><string>$ROOT</string>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP" >/dev/null
echo "$APP"
