#!/usr/bin/env bash
# remote-deploy.sh — deploy prforge plugin to 10.9.66.198
# Targets: .claude, .claude-openrouter, .claude-openrouter-2, .claude-openrouter-3
# Usage: ./remote-deploy.sh [--dry-run]

set -euo pipefail

REMOTE_HOST="10.9.66.198"
REMOTE_USER="bamn"
SRC="/home/bamn/prforge"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "[dry-run mode — no files will be written]"
fi

# Branch check
CURRENT_BRANCH=$(git -C "$(dirname "$0")" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
if [[ "$CURRENT_BRANCH" == "main" || "$CURRENT_BRANCH" == "master" ]]; then
  echo "ERROR: on protected branch '$CURRENT_BRANCH' — switch to a feature branch before deploying"
  exit 1
fi
echo "branch: $CURRENT_BRANCH"

# Remote profile dirs → local name mapping
declare -A PROFILES=(
  ["claude"]="/home/bamn/.claude"
  ["openclaude1"]="/home/bamn/.claude-openrouter"
  ["openclaude2"]="/home/bamn/.claude-openrouter-2"
  ["openclaude3"]="/home/bamn/.claude-openrouter-3"
)

RSYNC_OPTS=(-a --delete --exclude='.prforge/' --exclude='.remember/' --exclude='.git/' --exclude='sync.sh' --exclude='remote-deploy.sh')
if $DRY_RUN; then RSYNC_OPTS+=(--dry-run); fi

MARKETPLACE_JSON='{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "local",
  "description": "Local plugins for personal use",
  "owner": { "name": "b-a-m-n" },
  "plugins": [
    {
      "name": "prforge",
      "description": "Professional PR contribution harness — delegated execution with guarded release",
      "source": "./plugins/prforge",
      "category": "productivity"
    }
  ]
}'

ok=0
fail=0

for name in "${!PROFILES[@]}"; do
  PROFILE="${PROFILES[$name]}"
  PLUGINS_DIR="${PROFILE}/plugins"
  MKT_SRC="${PLUGINS_DIR}/marketplaces/local"
  MKT_PLUGIN_DIR="${PLUGINS_DIR}/marketplaces/local/plugins/prforge"
  CACHE_DIR="${PLUGINS_DIR}/cache/local/prforge/1.0.0"
  INSTALLED_JSON="${PLUGINS_DIR}/installed_plugins.json"
  KNOWN_JSON="${PLUGINS_DIR}/known_marketplaces.json"
  SETTINGS_JSON="${PROFILE}/settings.json"

  echo ""
  echo "=== $name ($PROFILE) ==="

  if $DRY_RUN; then
    echo "  [dry-run] would rsync to $MKT_PLUGIN_DIR"
    echo "  [dry-run] would rsync to $CACHE_DIR"
    echo "  [dry-run] would update $INSTALLED_JSON"
    echo "  [dry-run] would update $KNOWN_JSON"
    echo "  [dry-run] would update $SETTINGS_JSON"
    continue
  fi

  # 1. Create dirs
  ssh "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p '${MKT_SRC}/.claude-plugin' '${MKT_PLUGIN_DIR}' '${CACHE_DIR}'"

  # 2. Write marketplace.json
  ssh "${REMOTE_USER}@${REMOTE_HOST}" "echo '${MARKETPLACE_JSON}' > '${MKT_SRC}/.claude-plugin/marketplace.json'"

  # 3. rsync plugin files → marketplace source
  rsync "${RSYNC_OPTS[@]}" "${SRC}/" "${REMOTE_USER}@${REMOTE_HOST}:${MKT_PLUGIN_DIR}/" && echo "  OK  marketplace source"

  # 4. rsync plugin files → cache
  rsync "${RSYNC_OPTS[@]}" "${SRC}/" "${REMOTE_USER}@${REMOTE_HOST}:${CACHE_DIR}/" && echo "  OK  cache"

  # 5. Update known_marketplaces.json — add local entry
  ssh "${REMOTE_USER}@${REMOTE_HOST}" "python3 - '${KNOWN_JSON}' '${MKT_SRC}' <<'PYEOF'
import json, sys, os
path, mkt_path = sys.argv[1], sys.argv[2]
data = {}
if os.path.exists(path):
    data = json.load(open(path))
data['local'] = {
    'source': {'source': 'directory', 'path': mkt_path},
    'installLocation': mkt_path,
    'lastUpdated': '2026-05-03T00:00:00.000Z'
}
open(path, 'w').write(json.dumps(data, indent=2))
print('  OK  known_marketplaces.json')
PYEOF"

  # 6. Update installed_plugins.json — add prforge@local entry
  ssh "${REMOTE_USER}@${REMOTE_HOST}" "python3 - '${INSTALLED_JSON}' '${CACHE_DIR}' <<'PYEOF'
import json, sys, os
path, cache = sys.argv[1], sys.argv[2]
data = {'version': 2, 'plugins': {}}
if os.path.exists(path):
    data = json.load(open(path))
data['plugins']['prforge@local'] = [{
    'scope': 'user',
    'installPath': cache,
    'version': '1.0.0',
    'installedAt': '2026-05-03T00:00:00.000Z',
    'lastUpdated': '2026-05-03T00:00:00.000Z'
}]
open(path, 'w').write(json.dumps(data, indent=2))
print('  OK  installed_plugins.json')
PYEOF"

  # 7. Update settings.json — enable prforge@local
  ssh "${REMOTE_USER}@${REMOTE_HOST}" "python3 - '${SETTINGS_JSON}' <<'PYEOF'
import json, sys, os
path = sys.argv[1]
data = {}
if os.path.exists(path):
    data = json.load(open(path))
data.setdefault('enabledPlugins', {})['prforge@local'] = True
open(path, 'w').write(json.dumps(data, indent=2))
print('  OK  settings.json')
PYEOF"

  ((ok++)) || true
done

echo ""
if $DRY_RUN; then
  echo "dry-run complete — no changes made"
else
  echo "done: ${ok} profiles deployed, ${fail} failed"
fi
