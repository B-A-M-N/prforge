#!/usr/bin/env bash
# local-deploy.sh — sync prforge plugin to all local Claude Code instances
# Run this after any change to commands/, scripts/mesh/, or plugin.json
# Usage: bash local-deploy.sh [--dry-run]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true && echo "[dry-run]"

PROFILES=(
  "$HOME/.claude"
  "$HOME/.claude-openrouter"
  "$HOME/.claude-openrouter-1"
  "$HOME/.claude-openrouter-2"
  "$HOME/.claude-openrouter-3"
)

RSYNC_OPTS=(-a --delete)
$DRY_RUN && RSYNC_OPTS+=(--dry-run)

ok=0
for PROFILE in "${PROFILES[@]}"; do
  PDIR="$PROFILE/plugins/marketplaces/local/plugins/prforge"
  [ -d "$PDIR" ] || continue

  rsync "${RSYNC_OPTS[@]}" "$REPO/commands/"     "$PDIR/commands/"
  rsync "${RSYNC_OPTS[@]}" "$REPO/scripts/mesh/" "$PDIR/scripts/mesh/"
  $DRY_RUN || cp "$REPO/.claude-plugin/plugin.json" "$PDIR/plugin.json"
  echo "OK  $PROFILE"
  ok=$((ok + 1))
done

echo "Synced $ok profiles."
