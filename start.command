#!/bin/bash
# macOS: double-click this file to run Regulars.
cd "$(dirname "$0")/app" || exit 1
PORT="${1:-8010}"
echo
echo "  Regulars is starting on http://localhost:$PORT"
echo "  Leave this window open. Close it (or Ctrl-C) to stop."
echo
sleep 1
( open "http://localhost:$PORT" 2>/dev/null || true ) &
python3 server.py "$PORT"
