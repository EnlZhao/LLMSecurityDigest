#!/usr/bin/env bash
set -euo pipefail
PLIST_SRC="$(cd "$(dirname "$0")/.." && pwd)/launchd/com.llm-security-digest.daily.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.llm-security-digest.daily.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST_SRC" "$PLIST_DST"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
launchctl list | grep llm-security-digest || true
echo "installed: $PLIST_DST"
