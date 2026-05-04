#!/usr/bin/env bash
# PRForge Mesh — systemd service installer
# Calls meshctl.py to generate and optionally start services.
# Usage:
#   install_services.sh --mode local --node-id watchtower --start
#   install_services.sh --mode lan    --node-id forge-popos --start

set -euo pipefail

MESH_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE=""
NODE_ID=""
START=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)      MODE="$2"; shift 2 ;;
    --node-id)   NODE_ID="$2"; shift 2 ;;
    --start)     START=true; shift ;;
    *)          echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$MODE" || -z "$NODE_ID" ]]; then
  echo "Usage: install_services.sh --mode local|lan --node-id <id> [--start]"
  exit 1
fi

echo "Installing services for mode=$MODE node=$NODE_ID"

# Generate + optionally start via meshctl
if $START; then
  python3 "$MESH_SCRIPTS/meshctl.py" setup --mode "$MODE" --role "$([ "$NODE_ID" = "watchtower" ] && echo watchtower || echo forge)"
else
  python3 "$MESH_SCRIPTS/meshctl.py" status --mode "$MODE"
fi

echo ""
echo "Service installed. To start: /pr-distributed-local status (or /pr-distributed status)"
