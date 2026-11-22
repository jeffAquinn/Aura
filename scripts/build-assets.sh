#!/bin/sh
# Derive Aura's UI assets from assets/Aura.jpeg (the source of truth, never shipped as is).
#   scripts/build-assets.sh <output-dir>   ->  Avatar.png, AppIcon.icns
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$1"
mkdir -p "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Face-centred square crop of the 2752x1536 portrait (validated in the Sprint 3 design).
sips -s format png -c 1100 1100 --cropOffset 60 935 "$ROOT/assets/Aura.jpeg" --out "$TMP/square.png" >/dev/null

# Avatar: 256 px covers the 32 pt header avatar at 2x with room to spare.
sips -Z 256 "$TMP/square.png" --out "$OUT/Avatar.png" >/dev/null

# App icon (Finder, permission prompts, Activity Monitor).
ICONSET="$TMP/AppIcon.iconset"
mkdir "$ICONSET"
for s in 16 32 128 256 512; do
  sips -Z "$s" "$TMP/square.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -Z "$((s * 2))" "$TMP/square.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$OUT/AppIcon.icns"
