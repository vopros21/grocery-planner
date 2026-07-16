#!/usr/bin/env bash
# start.sh — launch the watcher and the review UI together.
# Usage: ./start.sh
# Stop:  Ctrl+C

set -e
cd "$(dirname "$0")"

# Use the venv's Python directly — no activation step required
PY="$(pwd)/venv/bin/python3"

if [ ! -x "$PY" ]; then
  echo "Error: venv not found at $(pwd)/venv"
  echo "Create it with: python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"
  exit 1
fi

# Ensure data/ exists
mkdir -p data inbox processed

UI_PORT="${GROCER_UI_PORT:-5001}"
UI_URL="http://localhost:${UI_PORT}"

# OSC 8 hyperlink escape sequence — makes the URL clickable in terminals
# that support it (Terminal.app, iTerm2, etc), not just plain text.
link() {
  printf '\033]8;;%s\033\\%s\033]8;;\033\\' "$1" "$1"
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🛒  Grocer — receipt processor"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Drop PDFs into:  $(pwd)/inbox/"
echo "  Review UI:       $(link "$UI_URL")"
echo "  Ctrl+C to stop"
echo ""

# Run watcher in background
"$PY" -u watcher.py &
WATCHER_PID=$!

# Make sure the watcher is killed no matter how this script exits
# (normal exit, Ctrl+C, or error)
trap 'kill $WATCHER_PID 2>/dev/null || true' EXIT INT TERM

# Auto-open the review UI in the default browser instead of relying on a
# clickable terminal link (Terminal.app on Sonoma and earlier doesn't
# support OSC 8 hyperlinks at all, so the link above is best-effort only).
# Skip with GROCER_NO_AUTOOPEN=1 if you don't want a tab popping open.
if [ -z "$GROCER_NO_AUTOOPEN" ]; then
  ( sleep 1.5 && open "$UI_URL" ) &
fi

# Run UI (blocking)
"$PY" -u ui.py
