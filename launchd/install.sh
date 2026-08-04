#!/usr/bin/env bash
# Install and start every LaunchAgent defined in launchd/*.plist for this checkout.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$REPO_DIR/logs"

for src in "$REPO_DIR"/launchd/*.plist; do
    label="$(basename "$src" .plist)"
    plist="$HOME/Library/LaunchAgents/$label.plist"

    sed "s|__REPO_DIR__|$REPO_DIR|g" "$src" > "$plist"

    launchctl unload "$plist" 2>/dev/null || true
    launchctl load -w "$plist"

    echo "Installed and started $label"
done

echo "Logs: $REPO_DIR/logs/"
