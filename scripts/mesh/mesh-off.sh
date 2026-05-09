#!/usr/bin/env bash
# PRForge mesh shutdown — stops all daemons on this machine.
set -euo pipefail

MESH_DIR="$HOME/.prforge-mesh"

for pf in "$MESH_DIR"/coordinator.pid "$MESH_DIR"/worker-*.pid; do
  [ -f "$pf" ] || continue
  PID=$(cat "$pf")
  if kill "$PID" 2>/dev/null; then
    echo "Stopped PID $PID ($pf)"
  else
    echo "Already gone: PID $PID ($pf)"
    rm -f "$pf"
  fi
done
echo "Done."
