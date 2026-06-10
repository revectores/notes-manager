#!/usr/bin/env bash
# Install and start the com.rex.notes-watch LaunchAgent for this checkout.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.rex.notes-watch.plist"

mkdir -p "$REPO_DIR/logs"

sed "s|__REPO_DIR__|$REPO_DIR|g" "$REPO_DIR/launchd/com.rex.notes-watch.plist" > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

echo "Installed and started com.rex.notes-watch (watching $REPO_DIR)"
echo "Logs: $REPO_DIR/logs/notes-watch.log"
