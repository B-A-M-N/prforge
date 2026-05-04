#!/usr/bin/env bash
# PRForge Discipline Check Hook
# Fires after Write/Edit/MultiEdit operations.
# Deterministic check: changed files vs contract, diff size, refactoring risk.
# Advisory: writes discipline_report.json, blocks via PRFORGE_EVENT if blocker found.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK_JSON=$(cat)

# --- Parse file_path ---
FILE_PATH=""
if command -v jq >/dev/null 2>&1; then
  FILE_PATH=$(echo "$HOOK_JSON" | jq -r '.tool_input.file_path // empty' 2>/dev/null || echo "")
elif command -v python3 >/dev/null 2>&1; then
  FILE_PATH=$(echo "$HOOK_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null || echo "")
fi

# Only act on files inside the repo, not .prforge artifacts
case "$FILE_PATH" in
  */.prforge/*|*/.prforge) exit 0 ;;
esac

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
HARNESS_DIR=$(python3 -c "
import os
p = '$REPO_ROOT'
candidates = [os.path.join(p, '.prforge'), os.path.join(os.path.dirname(p), '.prforge')]
for c in candidates:
    if os.path.isdir(c): print(c); break
" 2>/dev/null || exit 0)

CONTRACT="$HARNESS_DIR/contract.md"
PATCH_PLAN="$HARNESS_DIR/patch_plan.md"
DISC_REPORT="$HARNESS_DIR/discipline_report.json"

# If no contract, nothing to check
[ -f "$CONTRACT" ] || exit 0

# Run the Python checker
python3 "$SCRIPT_DIR/discipline-check.py" \
  --repo "$REPO_ROOT" \
  --harness "$HARNESS_DIR" \
  --contract "$CONTRACT" \
  --patch-plan "$PATCH_PLAN" \
  --report "$DISC_REPORT" 2>/dev/null || true

# Read result and emit event if blocked
if [ -f "$DISC_REPORT" ]; then
  STATUS=$(python3 -c "import json; d=json.load(open('$DISC_REPORT')); print(d.get('status',''))" 2>/dev/null || echo "")
  if [ "$STATUS" = "BLOCKED" ]; then
    echo "PRFORGE_EVENT|BLOCKER|Discipline check failed — see $DISC_REPORT"
  fi
fi

exit 0
