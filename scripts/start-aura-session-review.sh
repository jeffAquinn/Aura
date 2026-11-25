#!/bin/sh
# Launch Aura for a Claude-led, Aura-reviewed coding session.
set -eu

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app_path="$root_dir/app/build/Aura.app"

if [ ! -x "$app_path/Contents/MacOS/Aura" ]; then
    echo "Aura.app is not built; building the current project first..."
    "$root_dir/scripts/build-app.sh"
fi

if [ "${AURA_SESSION_REVIEW_DRY_RUN:-}" = "1" ]; then
    echo "Would launch: $app_path"
else
    open "$app_path"
    echo "Aura is launching. In your new Claude session, invoke: \$aura-session-review"
fi
