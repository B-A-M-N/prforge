#!/usr/bin/env bash
# local-deploy.sh — sync prforge plugin to all local Claude Code instances
# Run this after any change to commands/, scripts/mesh/, or plugin.json
# Usage: bash local-deploy.sh [--dry-run]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true && echo "[dry-run]"

RSYNC_OPTS=(-a --delete)
$DRY_RUN && RSYNC_OPTS+=(--dry-run)

ok=0

# Sync every directory that contains a copy of prforge_mesh.py
# (marketplace sources + all plugin caches — all must be current)
while IFS= read -r mesh_py; do
  MESH_DIR="$(dirname "$mesh_py")"
  PLUGIN_ROOT="$(dirname "$(dirname "$MESH_DIR")")"  # up from scripts/mesh

  rsync "${RSYNC_OPTS[@]}" "$REPO/scripts/mesh/" "$MESH_DIR/"

  CMD_DIR="$PLUGIN_ROOT/commands"
  if [ -d "$CMD_DIR" ]; then
    rsync "${RSYNC_OPTS[@]}" "$REPO/commands/" "$CMD_DIR/"
  fi

  PLUGIN_JSON="$PLUGIN_ROOT/plugin.json"
  if [ -f "$PLUGIN_JSON" ] && ! $DRY_RUN; then
    cp "$REPO/.claude-plugin/plugin.json" "$PLUGIN_JSON"
  fi

  echo "OK  $MESH_DIR"
  ok=$((ok + 1))
done < <(find "$HOME" -name "prforge_mesh.py" 2>/dev/null \
           | grep -v '\.git' \
           | grep 'plugins/' \
           | sort)

echo "Synced $ok plugin copies."
