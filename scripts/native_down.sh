#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="$REPO_ROOT/data/logs/native_pids.txt"
if [ ! -f "$PIDFILE" ]; then
  echo "[native_down] no pidfile, nothing to stop"
  exit 0
fi
while IFS=: read -r name pid; do
  if kill -0 "$pid" 2>/dev/null; then
    echo "[native_down] stopping $name (pid $pid)"
    kill "$pid" 2>/dev/null || true
  fi
done < "$PIDFILE"
sleep 1
rm -f "$PIDFILE"
