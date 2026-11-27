#!/bin/sh
# Derive Aura's UI assets from assets/Aura.jpeg (the source of truth, never shipped as is).
#   scripts/build-assets.sh <output-dir>   ->  Avatar.png, Starfield.png, Welcome.jpg, Header.png, AppIcon.icns
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$1"
mkdir -p "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Face-and-hair square crop of the 2752x1536 portrait (validated in the Sprint 3 design): app icon.
sips -s format png -c 1100 1100 --cropOffset 60 935 "$ROOT/assets/Aura.jpeg" --out "$TMP/square.png" >/dev/null

# Avatar: a tighter crop so the face stays recognisable in 22-30 pt circles; 256 px = 2x+.
sips -s format png -c 760 760 --cropOffset 215 1085 "$ROOT/assets/Aura.jpeg" --out "$TMP/face.png" >/dev/null
sips -Z 256 "$TMP/face.png" --out "$OUT/Avatar.png" >/dev/null

# "Aura Starfield" theme: the starfield at the right of the portrait (no face), blurred in-app.
sips -s format png -c 1536 752 --cropOffset 0 2000 "$ROOT/assets/Aura.jpeg" --out "$TMP/stars.png" >/dev/null
sips -Z 900 "$TMP/stars.png" --out "$OUT/Starfield.png" >/dev/null

# Welcome: shown while Aura starts and whenever the conversation is empty (assets/Welcome.jpeg).
sips -s format jpeg -s formatOptions 85 -Z 900 "$ROOT/assets/Welcome.jpeg" --out "$OUT/Welcome.jpg" >/dev/null

# Header: Aura's name written in stars, centered at the top of the panel (assets/Header.png).
# 480 px wide is enough for its ~80 pt width at 2x; the alpha channel is kept.
sips -s format png -Z 480 "$ROOT/assets/Header.png" --out "$OUT/Header.png" >/dev/null

# App icon (Finder, permission prompts, Activity Monitor).
ICONSET="$TMP/AppIcon.iconset"
mkdir "$ICONSET"
for s in 16 32 128 256 512; do
  sips -Z "$s" "$TMP/square.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -Z "$((s * 2))" "$TMP/square.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$OUT/AppIcon.icns"
