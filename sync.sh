#!/usr/bin/env bash
# sync.sh — sync prforge plugin to all Claude profiles (main + OR-1/2/3)
# Usage: ./sync.sh [--dry-run]

set -euo pipefail

SRC="/home/bamn/prforge"
DRY_RUN=""

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN="--dry-run"
  echo "[dry-run mode]"
fi

RSYNC_OPTS=(-a --delete --exclude='.prforge/' --exclude='.remember/' --exclude='.git/' --exclude='sync.sh' $DRY_RUN)

PRFORGE_ENTRY='{
  "name": "prforge",
  "description": "Professional PR contribution harness — delegated execution with guarded release",
  "source": "./plugins/prforge",
  "category": "productivity"
}'

# Idempotently ensure prforge is in a marketplace index JSON file.
# Does nothing if already present. Skipped in dry-run.
ensure_marketplace_entry() {
  local index_file="$1"
  if [[ ! -f "$index_file" ]]; then
    echo "  WARN  marketplace index not found: $index_file"
    return
  fi
  if [[ -n "$DRY_RUN" ]]; then
    python3 - "$index_file" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
names = [p["name"] for p in data.get("plugins", [])]
if "prforge" in names:
    print(f"  DRY   marketplace index already has prforge: {sys.argv[1]}")
else:
    print(f"  DRY   would add prforge to marketplace index: {sys.argv[1]}")
PYEOF
    return
  fi
  python3 - "$index_file" "$PRFORGE_ENTRY" <<'PYEOF'
import json, sys
path = sys.argv[1]
entry = json.loads(sys.argv[2])
data = json.load(open(path))
names = [p["name"] for p in data.get("plugins", [])]
if entry["name"] in names:
    print(f"  OK    marketplace index already has prforge: {path}")
else:
    data.setdefault("plugins", []).append(entry)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"  ADD   prforge added to marketplace index: {path}")
PYEOF
}

ok=0
fail=0

sync_profile() {
  local profile_label="$1"
  local profile_dir="$2"
  local marketplace_dst="${profile_dir}/plugins/marketplaces/local/plugins/prforge"
  local cache_dst="${profile_dir}/plugins/cache/local/prforge/1.0.0"
  local marketplace_index="${profile_dir}/plugins/marketplaces/local/.claude-plugin/marketplace.json"

  for DST in "$marketplace_dst" "$cache_dst"; do
    local label="${profile_label} $(basename "$(dirname "$DST")")/$(basename "$DST")"
    if [[ ! -d "$DST" ]]; then
      echo "  SKIP  $label (dir not found)"
      continue
    fi
    if rsync "${RSYNC_OPTS[@]}" "$SRC/" "$DST/"; then
      if [[ -z "$DRY_RUN" ]]; then
        if [[ -f "$DST/.claude-plugin/plugin.json" ]]; then
          cp "$DST/.claude-plugin/plugin.json" "$DST/plugin.json"
        fi
        rm -rf "$DST/.claude-plugin"
      fi
      echo "  OK    $label"
      ((ok++)) || true
    else
      echo "  FAIL  $label"
      ((fail++)) || true
    fi
  done

  ensure_marketplace_entry "$marketplace_index"
}

echo "==> main .claude"
sync_profile "main" "$HOME/.claude"

for n in 1 2 3; do
  echo "==> OR-${n}"
  sync_profile "OR${n}" "$HOME/.claude-openrouter-${n}"
done

echo ""
echo "done: ${ok} synced, ${fail} failed"
