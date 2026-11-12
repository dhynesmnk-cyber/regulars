#!/bin/sh
# Linux / any shell:  ./start.sh  [port]
cd "$(dirname "$0")/app" || exit 1
PORT="${1:-8010}"
echo "Regulars -> http://localhost:$PORT   (Ctrl-C to stop)"
exec python3 server.py "$PORT"
