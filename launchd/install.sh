#!/usr/bin/env bash
# Generate and load the daily launchd job from the template.
#
# Why this exists: launchd's plist format requires absolute paths for
# ProgramArguments, WorkingDirectory, StandardOutPath, and StandardErrorPath.
# The committed file `com.paperdaily.daily.plist.template` therefore uses the
# placeholder `${REPO_ROOT}`. This script replaces that placeholder with the
# absolute path of the current working directory and writes the rendered
# plist to `com.paperdaily.daily.plist` next to itself, then loads it into
# launchd under the current user.
#
# Usage:
#   ./launchd/install.sh           # render + load
#   ./launchd/install.sh --no-load # render only (review the file before loading)
#   ./launchd/install.sh --unload  # unload an existing job first
#
# Re-run after every `git pull` if the path changed.

set -euo pipefail

cd "$(dirname "$0")"          # now inside launchd/
REPO_ROOT="$(cd .. && pwd)"   # parent of launchd/
TEMPLATE="com.paperdaily.daily.plist.template"
PLIST="com.paperdaily.daily.plist"
LABEL="com.paperdaily.daily"

LOAD=1
RENDER_ONLY=0
UNLOAD_FIRST=0
for arg in "$@"; do
    case "$arg" in
        --no-load)  LOAD=0 ;;
        --render)   RENDER_ONLY=1; LOAD=0 ;;
        --unload)   UNLOAD_FIRST=1 ;;
        -h|--help)
            sed -n '3,16p' "$0"; exit 0 ;;
        *)
            echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# 1) Render
sed "s|\${REPO_ROOT}|${REPO_ROOT}|g" "$TEMPLATE" > "$PLIST"
plutil -lint "$PLIST" >/dev/null
echo "rendered $PLIST (repo root: $REPO_ROOT)"

# 2) Unload if requested
if [[ "$UNLOAD_FIRST" -eq 1 ]]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    echo "unloaded existing $LABEL"
fi

# 3) Load (unless render-only)
if [[ "$RENDER_ONLY" -eq 1 ]]; then
    echo "render-only mode; not loaded. Review $PLIST then re-run without --render."
    exit 0
fi

if [[ "$LOAD" -eq 1 ]]; then
    launchctl bootstrap "gui/$(id -u)" "$PWD/$PLIST" 2>&1 || \
        launchctl load -w "$PWD/$PLIST"
    echo "loaded $LABEL"
    launchctl print "gui/$(id -u)/$LABEL" | head -5 || true
fi