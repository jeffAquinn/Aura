#!/bin/sh
# Launch Aura for a Codex-led, phase-based Aura-reviewed coding session.
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
    echo "Aura is launching. In your new Codex session, invoke: \$aura-session-review"
    echo "The workflow now selects only relevant phases: orient/diagnose, specify, implement, verify, and hand off."
    echo "HOL remains Vault-only: auxiliary Aura skills are evidence inputs, not host commands a Developer can invoke."
fi
