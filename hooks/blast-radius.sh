#!/bin/bash
# PRForge Blast Radius Hook
# Fires automatically after Write/Edit operations.
# Computes blast radius metrics and updates state.json.
#
# Blast radius measures how far the change reaches beyond the intended scope.
# It is computed from:
#   - Number of files changed vs contract allowed files
#   - Depth of dependency chains (files that import changed files)
#   - Test coverage ratio (changed files with tests / total changed files)
#   - Public API surface touched
#
# This hook does NOT block — it only computes and records.
# The preflight hook and SKILL.md use these metrics to gate behavior.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hooks/prforge-common.sh
. "$SCRIPT_DIR/prforge-common.sh"

# Diagnostic: log hook invocation (minimal, for validation only)
mkdir -p "$(git rev-parse --show-toplevel 2>/dev/null)/.prforge" 2>/dev/null
echo "$(date -Iseconds) [blast-radius] Write/Edit hook fired" >> "$(git rev-parse --show-toplevel 2>/dev/null)/.prforge/hook_events.log" 2>/dev/null || true

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
HARNESS_DIR=$(prforge_artifact_dir "$REPO_ROOT")
prforge_ensure_pointer "$REPO_ROOT" "$HARNESS_DIR" || exit 0

STATE_FILE="$HARNESS_DIR/state.json"
if [ ! -f "$STATE_FILE" ]; then
  exit 0
fi

# Acquire exclusive lock on state.json to prevent concurrent hook writes
prforge_lock_state "$STATE_FILE" || exit 0

# ── Auto-discover MCP config (for context-mode detection) ──
MCP_CONFIG=""
for p in "$HOME/.claude/settings.json" "$HOME/.claude/mcp.json" "$REPO_ROOT/.mcp.json"; do
  [ -f "$p" ] && MCP_CONFIG="$p" && break
done

CONTRACT_FILE="$HARNESS_DIR/contract.md"

# --- Compute blast radius metrics ---

# 1. Files changed vs base
CHANGED_FILES=$(git diff --name-only 2>/dev/null | sort -u || true)
CHANGED_COUNT=$(echo "$CHANGED_FILES" | grep -c . || echo 0)

# 2. Contract allowed files
CONTRACT_FILES=""
if [ -f "$CONTRACT_FILE" ]; then
  CONTRACT_FILES=$(python3 -c "
import re
try:
    content = open('$CONTRACT_FILE').read()
    match = re.search(r'## Allowed Changes\s*\n(.*?)(?=\n##|\Z)', content, re.DOTALL)
    if match:
        files = [l.strip().lstrip('- ').strip() for l in match.group(1).splitlines() if l.strip()]
        print('\n'.join(files))
except:
    pass
" 2>/dev/null || echo "")
fi
CONTRACT_COUNT=$(echo "$CONTRACT_FILES" | grep -c . || echo 0)

# 3. Unexpected files (changed but not in contract)
UNEXPECTED_FILES=""
if [ -n "$CONTRACT_FILES" ] && [ -n "$CHANGED_FILES" ]; then
  UNEXPECTED_FILES=$(python3 -c "
import sys
changed = set(line.strip() for line in '''$CHANGED_FILES'''.splitlines() if line.strip())
allowed = set(line.strip() for line in '''$CONTRACT_FILES'''.splitlines() if line.strip())
# Check if each changed file matches any allowed pattern
unexpected = []
for f in changed:
    matched = False
    for pattern in allowed:
        if pattern and (pattern in f or f in pattern):
            matched = True
            break
    if not matched:
        unexpected.append(f)
if unexpected:
    print('\n'.join(sorted(unexpected)))
" 2>/dev/null || echo "")
fi
UNEXPECTED_COUNT=$(echo "$UNEXPECTED_FILES" | grep -c . || echo 0)

# 4. Test coverage ratio
TESTS_FOUND=""
if [ -n "$CHANGED_FILES" ]; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    dir=$(dirname "$f")
    base=$(basename "$f" | sed 's/\.[^.]*$//')
    found=$(find "$dir" -maxdepth 2 \( -name "${base}.test.*" -o -name "${base}.spec.*" -o -name "test_${base}.*" -o -name "${base}_test.*" \) 2>/dev/null | head -3 || true)
    if [ -n "$found" ]; then
      TESTS_FOUND="$TESTS_FOUND$found\n"
    fi
  done <<< "$CHANGED_FILES"
fi
TESTS_COUNT=$(echo -e "$TESTS_FOUND" | grep -c . || echo 0)

# 5. Dependency depth (files importing changed files — shallow scan)
# NOTE: When context-mode MCP is available, the agent should use
# context-mode__find_references for accurate dependency counts instead of this rg scan.
DEPENDENTS=""
if [ -n "$CHANGED_FILES" ] && command -v rg &> /dev/null; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    basename_mod=$(basename "$f" | sed 's/\.[^.]*$//')
    deps=$(rg -l "$basename_mod" --max-depth 3 -g '!node_modules' -g '!.git' -g '!.prforge' 2>/dev/null | grep -v "$f" | head -10 || true)
    if [ -n "$deps" ]; then
      DEPENDENTS="$DEPENDENTS$deps\n"
    fi
  done <<< "$CHANGED_FILES"
fi
DEPENDENTS_COUNT=$(echo -e "$DEPENDENTS" | grep -c . || echo 0)

# Check if context-mode MCP is available for better dependency analysis
CONTEXT_MODE_AVAILABLE=false
if [ -n "$MCP_CONFIG" ]; then
  CONTEXT_MODE_AVAILABLE=$(python3 -c "
import json
try:
    d = json.load(open('$MCP_CONFIG'))
    servers = d.get('mcpServers', {})
    print('true' if any('context' in k.lower() for k in servers) else 'false')
except:
    print('false')
" 2>/dev/null || echo "false")
fi

# 6. Public API surface (exports, public functions)
PUBLIC_API_TOUCHED="false"
if [ -n "$CHANGED_FILES" ]; then
  # Check if changed files contain exports/public API
  API_HITS=$(echo "$CHANGED_FILES" | xargs -I{} sh -c 'rg -l "export|module\.exports|public|pub " "{}" 2>/dev/null' 2>/dev/null | head -5 || true)
  if [ -n "$API_HITS" ]; then
    PUBLIC_API_TOUCHED="true"
  fi
fi

# 7. Compute blast radius score
# Low: 1-2 files, all in contract, tests exist, no dependents
# Medium: 3-5 files, mostly in contract, some tests
# High: 6+ files, unexpected files, no tests, many dependents, public API touched
BLAST_RADIUS="low"
if [ "$CHANGED_COUNT" -gt 5 ] || [ "$UNEXPECTED_COUNT" -gt 2 ] || [ "$DEPENDENTS_COUNT" -gt 5 ] || [ "$PUBLIC_API_TOUCHED" = "true" ]; then
  BLAST_RADIUS="high"
elif [ "$CHANGED_COUNT" -gt 2 ] || [ "$UNEXPECTED_COUNT" -gt 0 ] || [ "$DEPENDENTS_COUNT" -gt 2 ]; then
  BLAST_RADIUS="medium"
fi

# --- Update state.json ---
python3 -c "
import json, os
f = '$STATE_FILE'
d = json.load(open(f))

d.setdefault('blast_radius', {})
d['blast_radius'] = {
    'changed_files_count': $CHANGED_COUNT,
    'contract_files_count': $CONTRACT_COUNT,
    'unexpected_files_count': $UNEXPECTED_COUNT,
    'unexpected_files': [l for l in '''$UNEXPECTED_FILES'''.splitlines() if l.strip()],
    'tests_found_count': $TESTS_COUNT,
    'dependents_count': $DEPENDENTS_COUNT,
    'public_api_touched': '$PUBLIC_API_TOUCHED' == 'true',
    'score': '$BLAST_RADIUS',
    'computed_at': '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
}

# Auto-set scope_clean based on blast radius
if $UNEXPECTED_COUNT > 0:
    d.setdefault('scope', {}).setdefault('delta_check', {})['scope_clean'] = False
    d['scope']['delta_check']['unexpected_files'] = [l for l in '''$UNEXPECTED_FILES'''.splitlines() if l.strip()]
else:
    d.setdefault('scope', {}).setdefault('delta_check', {})['scope_clean'] = True
    d['scope']['delta_check']['unexpected_files'] = []

d['scope']['delta_check']['contract_files'] = [l for l in '''$CONTRACT_FILES'''.splitlines() if l.strip()]
d['scope']['delta_check']['actual_changed_files'] = [l for l in '''$CHANGED_FILES'''.splitlines() if l.strip()]

open(f, 'w').write(json.dumps(d, indent=2))
" 2>/dev/null || true

prforge_unlock_state "$STATE_FILE"
exit 0
