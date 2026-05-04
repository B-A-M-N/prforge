#!/usr/bin/env bash
# sync.sh — sync prforge plugin to all OR profiles
# Usage: ./sync.sh [--dry-run]

set -euo pipefail

SRC="/home/bamn/prforge"
FILES=(1 2 3)
DRY_RUN=""

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN="--dry-run"
  echo "[dry-run mode]"
fi

RSYNC_OPTS=(-a --delete --exclude='.prforge/' --exclude='.remember/' --exclude='.git/' --exclude='sync.sh' $DRY_RUN)

ok=0
fail=0

for n in "${FILES[@]}"; do
  PROFILE="$HOME/.claude-openrouter-${n}"
  MARKETPLACE_DST="${PROFILE}/plugins/marketplaces/local/plugins/prforge"
  CACHE_DST="${PROFILE}/plugins/cache/local/prforge/1.0.0"

  for DST in "$MARKETPLACE_DST" "$CACHE_DST"; do
    label="OR${n} $(basename "$(dirname "$DST")")/$(basename "$DST")"
    if [[ ! -d "$DST" ]]; then
      echo "  SKIP  $label (dir not found)"
      continue
    fi
    if rsync "${RSYNC_OPTS[@]}" "$SRC/" "$DST/"; then
      # Fix: move .claude-plugin/plugin.json to plugin.json at root
      if [[ -f "$DST/.claude-plugin/plugin.json" ]]; then
        cp "$DST/.claude-plugin/plugin.json" "$DST/plugin.json"
      fi
      rm -rf "$DST/.claude-plugin"
      echo "  OK    $label"
      ((ok++)) || true
    else
      echo "  FAIL  $label"
      ((fail++)) || true
    fi
  done
done

echo ""
echo "done: ${ok} synced, ${fail} failed"
