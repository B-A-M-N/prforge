#!/bin/bash
# PRForge Phase Boundary Enforcer
# Fires as PreToolUse on Write/Edit/MultiEdit — intercepts state.json writes and validates
# that the requested phase transition is in the allowed table.
# Exits 1 (blocking) on illegal jumps. Exits 0 otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hooks/prforge-common.sh
. "$SCRIPT_DIR/prforge-common.sh"

HOOK_JSON=$(cat)

# --- Parse file_path ---
FILE_PATH=""
if command -v jq >/dev/null 2>&1; then
  FILE_PATH=$(echo "$HOOK_JSON" | jq -r '.tool_input.file_path // empty' 2>/dev/null || echo "")
elif command -v python3 >/dev/null 2>&1; then
  FILE_PATH=$(echo "$HOOK_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null || echo "")
fi

# Only act on PRForge state.json writes. The authoritative state may live
# outside the repo under ${PRFORGE_HOME:-~/.prforge}/runs/...; repo-local
# .prforge is legacy.
case "$FILE_PATH" in
  */state.json) ;;
  *) exit 0 ;;
esac
PRFORGE_ROOT="${PRFORGE_HOME:-$HOME/.prforge}"
case "$FILE_PATH" in
  "$PRFORGE_ROOT"/*|*/.prforge/state.json|*/.prforge/runs/*/state.json) ;;
  *) exit 0 ;;
esac
HARNESS_DIR="$(dirname "$FILE_PATH")"

# --- Parse new phase from content being written/edited ---
CONTENT=""
if command -v jq >/dev/null 2>&1; then
  CONTENT=$(echo "$HOOK_JSON" | jq -r '.tool_input.content // empty' 2>/dev/null || echo "")
elif command -v python3 >/dev/null 2>&1; then
  CONTENT=$(echo "$HOOK_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('content',''))" 2>/dev/null || echo "")
fi

if [ -z "$CONTENT" ] && [ -f "$FILE_PATH" ] && command -v python3 >/dev/null 2>&1; then
  CONTENT=$(PRFORGE_HOOK_JSON="$HOOK_JSON" python3 - "$FILE_PATH" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    raw = path.read_text()
    hook = json.loads(os.environ.get("PRFORGE_HOOK_JSON", "{}"))
    tool_input = hook.get("tool_input") or {}

    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            old = edit.get("old_string")
            new = edit.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str) or old not in raw:
                print("")
                raise SystemExit(0)
            raw = raw.replace(old, new, 1)
        print(raw)
        raise SystemExit(0)

    old = tool_input.get("old_string")
    new = tool_input.get("new_string")
    if isinstance(old, str) and isinstance(new, str) and old in raw:
        print(raw.replace(old, new, 1))
    else:
        print("")
except Exception:
    print("")
PY
  2>/dev/null || echo "")
fi

NEW_PHASE=""
if command -v python3 >/dev/null 2>&1 && [ -n "$CONTENT" ]; then
  NEW_PHASE=$(echo "$CONTENT" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('phase', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")
fi

# state.json must remain parseable and phase-bearing; otherwise an Edit can bypass
# every phase gate by hiding the resulting phase from this hook.
if [ -z "$NEW_PHASE" ]; then
  echo ""
  echo "=== PRForge Phase Boundary Blocked ==="
  echo "Could not parse phase from pending state.json change."
  echo "Use a full valid state.json Write/Edit/MultiEdit that preserves the top-level phase field."
  exit 1
fi

STATE_SCHEMA_CHECK=$(TMP_STATE=$(mktemp "${TMPDIR:-/tmp}/prforge-pending-state.XXXXXX.json") && \
  printf "%s" "$CONTENT" > "$TMP_STATE" && \
  python3 "$SCRIPT_DIR/../scripts/prforge_state.py" migrate "$TMP_STATE" >/dev/null 2>&1 && \
  python3 "$SCRIPT_DIR/../scripts/prforge_state.py" validate "$TMP_STATE" 2>&1; \
  rc=$?; rm -f "$TMP_STATE"; exit $rc)
if ! echo "$STATE_SCHEMA_CHECK" | grep -qx "OK"; then
  echo ""
  echo "=== PRForge Phase Boundary Blocked ==="
  echo "Pending state.json does not match the PRForge state schema."
  echo "$STATE_SCHEMA_CHECK"
  exit 1
fi

# --- Read current phase from existing state.json ---
CURRENT_PHASE=""
if [ -f "$FILE_PATH" ] && command -v python3 >/dev/null 2>&1; then
  # Use prforge_state.py if available to read with lock, otherwise fallback
  if STATE_SCRIPT=$(prforge_state_py); then
    CURRENT_PHASE=$(python3 "$STATE_SCRIPT" read "$FILE_PATH" | python3 -c "import sys, json; print(json.load(sys.stdin).get('phase', ''))" 2>/dev/null || echo "")
  else
    prforge_lock_state "$FILE_PATH"
    CURRENT_PHASE=$(python3 -c "
import json
try:
    d = json.load(open('$FILE_PATH'))
    print(d.get('phase', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")
    prforge_unlock_state "$FILE_PATH"
  fi
fi

# If no existing state (new file) or same phase, allow
if [ -z "$CURRENT_PHASE" ] || [ "$CURRENT_PHASE" = "$NEW_PHASE" ]; then
  exit 0
fi

# --- Always allow: terminal blocker and recovery out of blocker/repair states ---
REPAIR_STATES=(
  "SCOPE_RECONCILE"
  "STATE_SYNC_REPAIR"
  "LEASE_RENEWAL_REPAIR"
  "REVIEW_REFRESH"
  "SCOPE_UPDATE"
  "PLAN_UPDATE"
  "VALIDATION_REPAIR"
  "INTELLIGENCE_REPAIR"
  "ARTIFACT_REPAIR"
  "COORDINATOR_RECONCILE"
  "STYLE_REPAIR"
  "COMMIT_REPAIR"
  "POLL_CI"
)

is_repair_state() {
  local p="$1"
  local s
  for s in "${REPAIR_STATES[@]}"; do
    [ "$s" = "$p" ] && return 0
  done
  return 1
}

if [ "$NEW_PHASE" = "BLOCKED" ] || [ "$CURRENT_PHASE" = "BLOCKED" ]; then
  exit 0
fi

# --- Allowed phase transition table ---
ALLOWED_TRANSITIONS=(
  "INTAKE:INVESTIGATE"
  "INVESTIGATE:PLAN"
  "INVESTIGATE:INTELLIGENCE_REPAIR"
  "PLAN:IMPLEMENT"
  "PLAN:SCOPE_UPDATE"
  "IMPLEMENT:VALIDATE"
  "IMPLEMENT:SCOPE_RECONCILE"
  "IMPLEMENT:PLAN_UPDATE"
  "VALIDATE:SELF_REVIEW"
  "VALIDATE:IMPLEMENT"          # corrective: tests failed, fix needed
  "VALIDATE:VALIDATION_REPAIR"
  "INTELLIGENCE_REPAIR:INVESTIGATE"
  "INTELLIGENCE_REPAIR:PLAN"
  "SCOPE_RECONCILE:IMPLEMENT"
  "SCOPE_RECONCILE:PLAN_UPDATE"
  "PLAN_UPDATE:IMPLEMENT"
  "SCOPE_UPDATE:PLAN"
  "SCOPE_UPDATE:IMPLEMENT"
  "VALIDATION_REPAIR:VALIDATE"
  "VALIDATION_REPAIR:IMPLEMENT"
  "ARTIFACT_REPAIR:SELF_REVIEW"
  "ARTIFACT_REPAIR:PACKAGE"
  "ARTIFACT_REPAIR:APPROVAL"
  "REVIEW_REFRESH:INVESTIGATE"
  "REVIEW_REFRESH:IMPLEMENT"
  "REVIEW_REFRESH:PACKAGE"
  "REVIEW_REFRESH:APPROVAL"
  "STATE_SYNC_REPAIR:IMPLEMENT"
  "STATE_SYNC_REPAIR:VALIDATE"
  "LEASE_RENEWAL_REPAIR:IMPLEMENT"
  "COORDINATOR_RECONCILE:IMPLEMENT"
  "COORDINATOR_RECONCILE:VALIDATE"
  "SELF_REVIEW:PACKAGE"
  "SELF_REVIEW:IMPLEMENT"       # corrective: audit found issues
  "SELF_REVIEW:ARTIFACT_REPAIR"
  "PACKAGE:APPROVAL"
  "PACKAGE:INVESTIGATE"         # corrective: review became stale mid-package
  "PACKAGE:REVIEW_REFRESH"
  "PACKAGE:ARTIFACT_REPAIR"
  "APPROVAL:POSTMORTEM"
  "POSTMORTEM:MEMORY_INDEX"
  "MEMORY_INDEX:COMPLETE"
  "APPROVAL:PACKAGE"            # corrective: approval fingerprint stale
  "APPROVAL:INVESTIGATE"        # corrective: new review comments since last fetch
  "APPROVAL:REVIEW_REFRESH"
  "APPROVAL:ARTIFACT_REPAIR"
  "PACKAGE:STYLE_REPAIR"
  "APPROVAL:POLL_CI"
  "APPROVAL:COMMIT_REPAIR"
  "APPROVAL:STYLE_REPAIR"
  "POLL_CI:POSTMORTEM"
  "POLL_CI:APPROVAL"
  "POLL_CI:PACKAGE"
  "COMMIT_REPAIR:APPROVAL"
  "COMMIT_REPAIR:PACKAGE"
  "COMMIT_REPAIR:SELF_REVIEW"
  "STYLE_REPAIR:APPROVAL"
  "STYLE_REPAIR:IMPLEMENT"
  "STYLE_REPAIR:VALIDATE"
  "COMPLETE:BLOCKED"
)

if is_repair_state "$NEW_PHASE"; then
  exit 0
fi

TRANSITION="${CURRENT_PHASE}:${NEW_PHASE}"

if [ "$TRANSITION" = "INVESTIGATE:PLAN" ] || [ "$TRANSITION" = "INTELLIGENCE_REPAIR:PLAN" ]; then
  HARNESS_DIR="$(dirname "$FILE_PATH")"
  INTELLIGENCE_CHECK=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - "$HARNESS_DIR" <<'PY'
import json
import os
import sys
from pathlib import Path

artifact_dir = Path(sys.argv[1])

try:
    state = json.loads(os.environ.get("PRFORGE_STATE_CONTENT", ""))
except Exception as exc:
    print(f"FAIL:state_json_unreadable:{exc}")
    raise SystemExit(0)

intel = state.get("intelligence") or {}
evidence = intel.get("evidence") or {}
calls = set(evidence.get("gitnexus_calls") or intel.get("gitnexus_calls") or [])
fallback = evidence.get("fallback_commands") or intel.get("fallback_commands") or []
missing = []

if "gitnexus_available" not in intel:
    missing.append("record gitnexus_available=true/false")
elif intel.get("gitnexus_available") is True:
    if intel.get("gitnexus_probe_attempted") is not True and "list_repos" not in calls:
        missing.append("record gitnexus_probe_attempted=true or gitnexus_calls includes list_repos")
    required = {"list_repos", "query", "impact", "context", "detect_changes"}
    missing_calls = sorted(required - calls)
    if missing_calls:
        missing.append("record GitNexus calls: " + ", ".join(missing_calls))
    if not evidence.get("primary_target"):
        missing.append("record intelligence.evidence.primary_target")
    if not evidence.get("key_symbol"):
        missing.append("record intelligence.evidence.key_symbol")
else:
    if not intel.get("unavailable_reason") and not intel.get("disclosure"):
        missing.append("record why GitNexus is unavailable")
    if not intel.get("minimum_risk_floor"):
        missing.append("record degraded-mode minimum_risk_floor")
    if len(fallback) < 3:
        missing.append("record at least 3 fallback intelligence commands")

repo_intel = artifact_dir / "repo_intelligence.md"
if not repo_intel.exists():
    missing.append("write repo_intelligence.md")
else:
    text = repo_intel.read_text(errors="replace")
    if len(text.strip()) < 200:
        missing.append("repo_intelligence.md is too thin")
    if intel.get("gitnexus_available") is True and "GitNexus" not in text:
        missing.append("repo_intelligence.md must include GitNexus results")

if missing:
    print("FAIL:" + " | ".join(missing))
else:
    print("OK")
PY
)

  if ! echo "$INTELLIGENCE_CHECK" | grep -qx "OK"; then
    REPO_ROOT=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - <<'PY'
import json
import os
import sys
try:
    d = json.loads(os.environ.get("PRFORGE_STATE_CONTENT", ""))
    print(d.get("repo", {}).get("local_path", ""))
except Exception:
    print("")
PY
)
    [ -n "$REPO_ROOT" ] || REPO_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null || pwd)
    OBJECTIVE=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - <<'PY'
import json
import os
import sys
try:
    d = json.loads(os.environ.get("PRFORGE_STATE_CONTENT", ""))
    print(d.get("task", {}).get("objective", "unknown"))
except Exception:
    print("unknown")
PY
)
    prforge_write_redirect "$REPO_ROOT" "$HARNESS_DIR" "missing_intelligence_evidence" "INVESTIGATE->PLAN" "$FILE_PATH" "INVESTIGATE" "complete GitNexus probe/evidence or documented degraded fallback, then retry PLAN" "INVESTIGATE" "$OBJECTIVE" || true
    echo ""
    echo "=== PRForge Intelligence Gate Redirect ==="
    prforge_redirect_message "INVESTIGATE->PLAN" "${INTELLIGENCE_CHECK#FAIL:}" "reads, GitNexus/context-mode calls, gh queries, local fallback searches, repo_intelligence.md updates" "complete intelligence evidence in state.json and repo_intelligence.md; use INTELLIGENCE_REPAIR if you need an explicit repair state" "INVESTIGATE"
    exit 1
  fi
fi

if [ "$TRANSITION" = "IMPLEMENT:VALIDATE" ]; then
  HARNESS_DIR="$(dirname "$FILE_PATH")"
  REPO_ROOT=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - <<'PY'
import json
import os
try:
    d = json.loads(os.environ.get("PRFORGE_STATE_CONTENT", ""))
    print(d.get("repo", {}).get("local_path", ""))
except Exception:
    print("")
PY
)
  [ -n "$REPO_ROOT" ] || REPO_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null || pwd)
  POLICY_JSON=$(python3 "$SCRIPT_DIR/../scripts/mesh/policy_engine.py" check --event phase_exit --phase IMPLEMENT --run-dir "$HARNESS_DIR" --repo "$REPO_ROOT" --write 2>/tmp/policy_engine_stderr.log || echo "__POLICY_ERROR__")
  
  if echo "$POLICY_JSON" | grep -q "^__POLICY_ERROR__"; then
    echo "⚠️ Policy engine failed — see /tmp/policy_engine_stderr.log" >&2
    echo "Blocking phase transition due to policy engine failure."
    exit 1
  fi
  
  POLICY_DECISION=$(printf "%s" "$POLICY_JSON" | python3 -c "import json,sys; print((json.load(sys.stdin) if not sys.stdin.isatty() else {}).get('decision',''))" 2>/dev/null || echo "")
  if [ "$POLICY_DECISION" = "redirect_recoverable" ]; then
    REDIRECT_STATE=$(printf "%s" "$POLICY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('redirect_state','VALIDATION_REPAIR'))" 2>/dev/null || echo "VALIDATION_REPAIR")
    REASON=$(printf "%s" "$POLICY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reason','policy_redirect'))" 2>/dev/null || echo "policy_redirect")
    NEXT=$(printf "%s" "$POLICY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('required_next_action','Resolve policy redirect.'))" 2>/dev/null || echo "Resolve policy redirect.")
    OBJECTIVE=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - <<'PY'
import json
import os
try:
    d = json.loads(os.environ.get("PRFORGE_STATE_CONTENT", ""))
    print(d.get("task", {}).get("objective", "unknown"))
except Exception:
    print("unknown")
PY
)
    prforge_write_redirect "$REPO_ROOT" "$HARNESS_DIR" "$REASON" "IMPLEMENT->VALIDATE" "$FILE_PATH" "IMPLEMENT" "$NEXT" "IMPLEMENT" "$OBJECTIVE" || true
    echo ""
    echo "=== PRForge Policy Redirect ==="
    prforge_redirect_message "IMPLEMENT->VALIDATE" "$REASON" "approved edits, reads, tests, validation repair, scope reconcile" "$NEXT; suggested repair state: $REDIRECT_STATE" "IMPLEMENT"
    exit 1
  fi

  # --- Review decomposition enforcement ---
  # If review_decomposition.md exists, ALL required items must be marked complete
  REVIEW_DECOMP="$HARNESS_DIR/review_decomposition.md"
  if [ -f "$REVIEW_DECOMP" ]; then
    REVIEW_CHECK=$(python3 - "$REVIEW_DECOMP" <<'PY'
import sys, re
path = sys.argv[1]
content = open(path, errors="replace").read()

# Count required items (lines with [ ] or - [ ] that are not already checked)
# and completed items (lines with [x] or - [x])
required_not_done = 0
required_done = 0
optional_not_done = 0

for line in content.split("\n"):
    line = line.strip()
    # Required items: [ ] checkbox not checked, or lines marked as required_change/blocker
    if re.match(r'^[-*]\s*\[\s\]', line):
        # Check if it's marked as required (not optional)
        if any(tag in line.lower() for tag in ['required', 'blocker', 'must', 'change']):
            required_not_done += 1
        else:
            optional_not_done += 1
    elif re.match(r'^[-*]\s*\[x\]', line):
        if any(tag in line.lower() for tag in ['required', 'blocker', 'must', 'change']):
            required_done += 1
        # Count all checked items
        required_done += 0  # already counted above

# Also check for explicit status markers
if 'status: complete' in content.lower() or 'status: addressed' in content.lower():
    # If the file explicitly marks items as complete, trust that
    pass

# Check for "all items addressed" or similar summary
if required_not_done > 0:
    print(f"FAIL:{required_not_done} required review items not addressed")
else:
    print("OK")
PY
)
    if ! echo "$REVIEW_CHECK" | grep -qx "OK"; then
      echo ""
      echo "=== PRForge Review Gate ==="
      echo "IMPLEMENT → VALIDATE blocked: ${REVIEW_CHECK#FAIL:}"
      echo ""
      echo "All required reviewer comments must be addressed before advancing."
      echo "Review: $REVIEW_DECOMP"
      exit 1
    fi
  fi
fi

# --- INTAKE → INVESTIGATE: mode must be set before investigation begins ---
if [ "$TRANSITION" = "INTAKE:INVESTIGATE" ]; then
  HARNESS_DIR="$(dirname "$FILE_PATH")"
  MODE_CHECK=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - <<'PY'
import json, os
try:
    d = json.loads(os.environ.get("PRFORGE_STATE_CONTENT", ""))
    mode = d.get("mode", "")
    valid_modes = {"new_pr", "review_response", "pr_polish", "ci_fix",
                   "candidate_discovery", "local_task"}
    if not mode:
        print("FAIL:state.mode is not set — read the matching mode file from $SKILL_ROOT/modes/ and set mode before investigating")
    elif mode not in valid_modes:
        print(f"FAIL:mode '{mode}' is not a known mode. Valid: {', '.join(sorted(valid_modes))}")
    else:
        print("OK")
except Exception as e:
    print(f"FAIL:could not read state content: {e}")
PY
)
  if ! echo "$MODE_CHECK" | grep -qx "OK"; then
    REPO_ROOT=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - <<'PY'
import json, os
try:
    print(json.loads(os.environ.get("PRFORGE_STATE_CONTENT","")).get("repo",{}).get("local_path",""))
except Exception:
    print("")
PY
)
    [ -n "$REPO_ROOT" ] || REPO_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null || pwd)
    OBJECTIVE=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - <<'PY'
import json, os
try:
    print(json.loads(os.environ.get("PRFORGE_STATE_CONTENT","")).get("task",{}).get("objective","unknown"))
except Exception:
    print("unknown")
PY
)
    prforge_write_redirect "$REPO_ROOT" "$HARNESS_DIR" "mode_not_set" \
      "INTAKE->INVESTIGATE" "$FILE_PATH" "INTAKE" \
      "set state.json mode field; read matching mode file from \$SKILL_ROOT/modes/; then retry INVESTIGATE transition" \
      "INTAKE" "$OBJECTIVE" || true
    echo ""
    echo "=== PRForge Mode Gate Redirect ==="
    prforge_redirect_message "INTAKE->INVESTIGATE" "${MODE_CHECK#FAIL:}" \
      "reads, mode file discovery, state.json mode field update" \
      "find and read the correct mode file (\$SKILL_ROOT/modes/<mode>.md), set state.json mode=<mode>, then retry" \
      "INTAKE"
    exit 1
  fi
fi

# --- VALIDATE → SELF_REVIEW: validation must be complete and ledger written ---
if [ "$TRANSITION" = "VALIDATE:SELF_REVIEW" ]; then
  HARNESS_DIR="$(dirname "$FILE_PATH")"
  VALIDATE_CHECK=$(TMP_STATE=$(mktemp "${TMPDIR:-/tmp}/prforge-state.XXXXXX.json") && \
    printf "%s" "$CONTENT" > "$TMP_STATE" && \
    python3 "$SCRIPT_DIR/../scripts/validation_evidence.py" "$HARNESS_DIR" --state-file "$TMP_STATE" 2>/dev/null; \
    rc=$?; rm -f "$TMP_STATE"; exit $rc)
  if ! echo "$VALIDATE_CHECK" | grep -qx "OK"; then
    REPO_ROOT=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - <<'PY'
import json, os
try:
    print(json.loads(os.environ.get("PRFORGE_STATE_CONTENT","")).get("repo",{}).get("local_path",""))
except Exception:
    print("")
PY
)
    [ -n "$REPO_ROOT" ] || REPO_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null || pwd)
    OBJECTIVE=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - <<'PY'
import json, os
try:
    print(json.loads(os.environ.get("PRFORGE_STATE_CONTENT","")).get("task",{}).get("objective","unknown"))
except Exception:
    print("unknown")
PY
)
    prforge_write_redirect "$REPO_ROOT" "$HARNESS_DIR" "validation_incomplete" \
      "VALIDATE->SELF_REVIEW" "$FILE_PATH" "VALIDATE" \
      "run all contract.md validation commands; record results in validation_ledger.md; fix any failures; then retry SELF_REVIEW transition" \
      "VALIDATE" "$OBJECTIVE" || true
    echo ""
    echo "=== PRForge Validation Gate Redirect ==="
    prforge_redirect_message "VALIDATE->SELF_REVIEW" "${VALIDATE_CHECK#FAIL:}" \
      "running validation commands, writing validation_ledger.md, fixing test failures, state.json updates" \
      "run all commands in contract.md, write results to validation_ledger.md, fix failures, then retry" \
      "VALIDATE"
    exit 1
  fi
fi

# --- SELF_REVIEW → PACKAGE: hostile review must exist with a PASS verdict ---
if [ "$TRANSITION" = "SELF_REVIEW:PACKAGE" ]; then
  HARNESS_DIR="$(dirname "$FILE_PATH")"
  REVIEW_CHECK=$(python3 - "$HARNESS_DIR" <<'PY'
import sys, re
from pathlib import Path

artifact_dir = Path(sys.argv[1])
missing = []
findings = []

hostile = artifact_dir / "hostile_review.md"
if not hostile.exists():
    missing.append("hostile_review.md not found — load self_review.md playbook and answer all 10 audit questions")
else:
    text = hostile.read_text(errors="replace")
    if len(text.strip()) < 100:
        missing.append("hostile_review.md is too thin — answer all 10 hostile audit questions with evidence")
    elif "PASS" not in text.upper():
        missing.append("hostile_review.md has no PASS verdict — resolve all NEEDS_FIX items and record final verdict")
    else:
        # Rigorous check: verify the hostile review actually addresses review items
        review_decomp = artifact_dir / "review_decomposition.md"
        if review_decomp.exists():
            decomp_text = review_decomp.read_text(errors="replace")
            # Count required items in review decomposition
            required_items = re.findall(r'(?:required_change|blocker|must_fix|needs_fix)', decomp_text, re.IGNORECASE)
            required_count = len(required_items)
            
            if required_count > 0:
                # Check that hostile review references these items
                # Each required item should have a corresponding finding in hostile review
                # Look for item IDs, references to specific files, or issue numbers
                item_refs = re.findall(r'(?:R\d+|Q\d+|item\s+\d+|finding\s+\d+)', text, re.IGNORECASE)
                
                # Check that the hostile review has actual findings (not just "all good")
                has_findings = any(marker in text.lower() for marker in [
                    'finding:', 'issue:', 'concern:', 'risk:', 'problem:',
                    'addressed:', 'resolved:', 'fixed:', 'verified:'
                ])
                
                # Check for per-item coverage
                if required_count > 2 and len(item_refs) < required_count // 2:
                    missing.append(f"hostile_review.md PASS but only references {len(item_refs)} of {required_count} required review items — each item needs explicit coverage")
                elif not has_findings and required_count > 0:
                    missing.append("hostile_review.md PASS but contains no actual findings — each review item needs a finding (even if 'no issue found')")

print("FAIL:" + " | ".join(missing) if missing else "OK")
PY
)
  if ! echo "$REVIEW_CHECK" | grep -qx "OK"; then
    REPO_ROOT=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - <<'PY'
import json, os
try:
    print(json.loads(os.environ.get("PRFORGE_STATE_CONTENT","")).get("repo",{}).get("local_path",""))
except Exception:
    print("")
PY
)
    [ -n "$REPO_ROOT" ] || REPO_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null || pwd)
    OBJECTIVE=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - <<'PY'
import json, os
try:
    print(json.loads(os.environ.get("PRFORGE_STATE_CONTENT","")).get("task",{}).get("objective","unknown"))
except Exception:
    print("unknown")
PY
)
    prforge_write_redirect "$REPO_ROOT" "$HARNESS_DIR" "hostile_review_incomplete" \
      "SELF_REVIEW->PACKAGE" "$FILE_PATH" "SELF_REVIEW" \
      "read self_review.md playbook; write hostile_review.md with all 10 audit questions answered; set verdict=PASS; then retry PACKAGE transition" \
      "SELF_REVIEW" "$OBJECTIVE" || true
    echo ""
    echo "=== PRForge Self-Review Gate Redirect ==="
    prforge_redirect_message "SELF_REVIEW->PACKAGE" "${REVIEW_CHECK#FAIL:}" \
      "reads, hostile_review.md writing, audit question answers, state.json updates" \
      "read self_review.md playbook fully; answer all 10 questions in hostile_review.md; set final verdict=PASS; then retry" \
      "SELF_REVIEW"
    exit 1
  fi
fi


# --- Review Item → Git Diff Verification ---
# For IMPLEMENT→VALIDATE and SELF_REVIEW→PACKAGE transitions,
# verify that files mentioned in review_decomposition were actually modified
if [ "$TRANSITION" = "IMPLEMENT:VALIDATE" ] || [ "$TRANSITION" = "SELF_REVIEW:PACKAGE" ]; then
  HARNESS_DIR="$(dirname "$FILE_PATH")"
  REVIEW_DECOMP="$HARNESS_DIR/review_decomposition.md"
  if [ -f "$REVIEW_DECOMP" ]; then
    DIFF_CHECK=$(python3 - "$HARNESS_DIR" <<'PY'
import sys, re, subprocess
from pathlib import Path

artifact_dir = Path(sys.argv[1])
decomp_file = artifact_dir / "review_decomposition.md"

if not decomp_file.exists():
    print("OK")
    raise SystemExit(0)

decomp = decomp_file.read_text(errors="replace")

# Extract file paths mentioned in review items
# Look for patterns like "file.ts", "src/foo/bar.ts", paths in backticks, etc.
mentioned_files = set()
# Match file paths in backticks
mentioned_files.update(re.findall(r'`([^`]+\.(?:ts|js|py|go|rs|java|rb|cpp|c|h|md|json|yaml|yml|toml|cfg|conf))`', decomp, re.IGNORECASE))
# Match file paths after "File:" or "In " patterns
mentioned_files.update(re.findall(r'(?:File|In|Path):\s*([^\s:]+\.(?:ts|js|py|go|rs|java|rb|cpp|c|h|md|json|yaml|yml|toml|cfg|conf))', decomp, re.IGNORECASE))
# Match bare file paths in review items
mentioned_files.update(re.findall(r'(?:^|\s)([a-zA-Z0-9_/-]+\.(?:ts|js|py|go|rs|java|rb|cpp|c|h))\s', decomp))

# Filter to only required/blocker items
required_sections = re.findall(r'(?:required_change|blocker|must_fix).*?(?=###|\Z)', decomp, re.IGNORECASE | re.DOTALL)
required_text = '\n'.join(required_sections)

required_files = set()
for f in mentioned_files:
    # Check if this file is mentioned in a required section
    if f in required_text or any(f in section for section in required_sections):
        required_files.add(f)

if not required_files:
    print("OK")
    raise SystemExit(0)

# Check git diff for these files
try:
    diff_output = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~5..HEAD"],
        capture_output=True, text=True, timeout=10,
        cwd=str(artifact_dir)
    ).stdout.strip()
    
    if not diff_output:
        # Try broader diff
        diff_output = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, timeout=10,
            cwd=str(artifact_dir)
        ).stdout.strip()
    
    changed_files = set(diff_output.split('\n')) if diff_output else set()
    
    # Check if required files were actually modified
    missing_files = []
    for req_file in required_files:
        # Check if any changed file matches or contains the required file
        if not any(req_file in changed or changed in req_file for changed in changed_files):
            missing_files.append(req_file)
    
    if missing_files:
        print(f"FAIL:Review items reference files not modified in this branch: {', '.join(list(missing_files)[:5])}")
    else:
        print("OK")
except Exception as e:
    print(f"OK")  # Don't block on git errors
PY
)
    if ! echo "$DIFF_CHECK" | grep -qx "OK"; then
      echo ""
      echo "=== PRForge Review-Diff Gate ==="
      echo "${DIFF_CHECK#FAIL:}"
      echo ""
      echo "Reviewer comments reference files that were not modified."
      echo "Either address these items or explicitly document why they don't require code changes."
      exit 1
    fi
  fi
fi

# --- APPROVAL → POSTMORTEM: terminal snapshot and artifact enforcement ---
if [ "$TRANSITION" = "APPROVAL:POSTMORTEM" ]; then
  HARNESS_DIR="$(dirname "$FILE_PATH")"
  RUN_DIR=""

  # Determine run_dir from state
  if command -v python3 >/dev/null 2>&1 && [ -n "$CONTENT" ]; then
    RUN_DIR=$(echo "$CONTENT" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    mc = d.get('memory_context', {})
    rid = mc.get('memory_run_id', '')
    if rid:
        print(d.get('repo',{}).get('local_path','') + '/.prforge/runs/' + rid)
except Exception:
    print('')
" 2>/dev/null || echo "")
  fi

  # Fallback: try to find run dir
  if [ -z "$RUN_DIR" ] || [ ! -d "$RUN_DIR" ]; then
    RUN_DIR=$(find "$HARNESS_DIR" -maxdepth 3 -name "state.json" -path "*/runs/*" 2>/dev/null | head -1 | xargs dirname 2>/dev/null || echo "")
  fi

  if [ -z "$RUN_DIR" ] || [ ! -d "$RUN_DIR" ]; then
    echo ""
    echo "=== PRForge Memory Gate ==="
    echo "APPROVAL → POSTMORTEM blocked: cannot find run directory."
    echo "Expected: .prforge/runs/<memory_run_id>/"
    echo "Ensure memory_context.memory_run_id is set in state.json."
    exit 1
  fi

  # Check outcome is set
  OUTCOME=$(echo "$CONTENT" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get('outcome', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")

  if [ -z "$OUTCOME" ] || [ "$OUTCOME" = "None" ]; then
    echo ""
    echo "=== PRForge Memory Gate ==="
    echo "APPROVAL → POSTMORTEM blocked: outcome not set."
    echo "Set state.json outcome to one of: MERGED, CLOSED, ABANDONED, REVERTED."
    exit 1
  fi

  # Run terminal_snapshot.py
  SNAPSHOT_RESULT=$(python3 "$SCRIPT_DIR/../scripts/terminal_snapshot.py" --run-dir "$RUN_DIR" --state "$FILE_PATH" 2>&1)
  SNAPSHOT_RC=$?

  if [ $SNAPSHOT_RC -ne 0 ]; then
    echo ""
    echo "=== PRForge Memory Gate ==="
    echo "APPROVAL → POSTMORTEM blocked: terminal snapshot failed."
    echo "$SNAPSHOT_RESULT"
    exit 1
  fi

  echo "Terminal snapshot captured for POSTMORTEM transition."
fi

# --- Commit Hygiene Check (all transitions) ---
# Block phase advancement if any commits in this branch contain Co-authored-by
COMMIT_HYGIENE=$(python3 - "$HARNESS_DIR" <<'PY'
import subprocess, sys
from pathlib import Path

harness_dir = Path(sys.argv[1])

# Find the base ref
base_ref = ""
for candidate in ["upstream/main", "upstream/master", "origin/main", "origin/master"]:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", candidate],
        capture_output=True, text=True, timeout=5,
        cwd=str(harness_dir)
    )
    if result.returncode == 0:
        base_ref = candidate
        break

if not base_ref:
    # Try to find any reasonable base
    result = subprocess.run(
        ["git", "merge-base", "HEAD", "main"],
        capture_output=True, text=True, timeout=5,
        cwd=str(harness_dir)
    )
    if result.returncode == 0:
        base_ref = result.stdout.strip()

if not base_ref:
    print("OK")  # Can't determine base, skip check
    raise SystemExit(0)

# Check commits for Co-authored-by and other violations
result = subprocess.run(
    ["git", "log", f"{base_ref}..HEAD", "--format=%H %s%n%b---COMMIT_END---"],
    capture_output=True, text=True, timeout=10,
    cwd=str(harness_dir)
)

if result.returncode != 0 or not result.stdout.strip():
    print("OK")
    raise SystemExit(0)

commits = result.stdout.split("---COMMIT_END---")
violations = []

ai_patterns = [
    "Co-authored-by",
    "Generated by Claude",
    "Generated with Claude",
    "AI-generated",
    "AI-assisted",
    "Claude Code",
    "Anthropic",
]

for commit in commits:
    commit = commit.strip()
    if not commit:
        continue
    for pattern in ai_patterns:
        if pattern.lower() in commit.lower():
            # Extract commit hash
            lines = commit.split("\n")
            commit_hash = lines[0].split()[0] if lines else "?"
            violations.append(f"{commit_hash[:8]}: contains '{pattern}'")
            break

if violations:
    print("FAIL:" + "; ".join(violations))
else:
    print("OK")
PY
)

if ! echo "$COMMIT_HYGIENE" | grep -qx "OK"; then
  echo ""
  echo "=== PRForge Commit Hygiene Gate ==="
  echo "Phase transition blocked: commit hygiene violations detected."
  echo ""
  echo "$COMMIT_HYGIENE" | sed 's/FAIL://' | tr ';' '\n' | while read -r v; do
    [ -n "$v" ] && echo "  ✗ $v"
  done
  echo ""
  echo "Fix: git rebase -i to remove Co-authored-by trailers and AI attribution."
  echo "Then retry the phase transition."
  exit 1
fi

# --- Loop Detection / Circuit Breaker ---
# Track phase transition attempts to detect infinite loops
# If the same transition fails N times in a row, block and escalate to user
LOOP_TRACKER="$HARNESS_DIR/.prforge/loop_tracker.json"
mkdir -p "$(dirname "$LOOP_TRACKER")" 2>/dev/null

TRANSITION_KEY="${CURRENT_PHASE}:${NEW_PHASE}"
LOOP_COUNT=0
MAX_LOOP_ATTEMPTS=3

if [ -f "$LOOP_TRACKER" ] && command -v python3 >/dev/null 2>&1; then
  LOOP_CHECK=$(python3 - "$LOOP_TRACKER" "$TRANSITION_KEY" "$MAX_LOOP_ATTEMPTS" <<'PY'
import json, sys, time
from pathlib import Path

tracker_path = Path(sys.argv[1])
transition_key = sys.argv[2]
max_attempts = int(sys.argv[3])

# Load existing tracker
tracker = {}
if tracker_path.exists():
    try:
        tracker = json.load(open(tracker_path))
    except:
        tracker = {}

# Clean up old entries (older than 1 hour)
now = time.time()
tracker = {k: v for k, v in tracker.items() if now - v.get("last_attempt", 0) < 3600}

# Check loop count for this transition
entry = tracker.get(transition_key, {"count": 0, "last_attempt": 0})
count = entry["count"]

if count >= max_attempts:
    print(f"LOOP_DETECTED:{count}")
else:
    print("OK")

# Update tracker
tracker[transition_key] = {"count": count + 1, "last_attempt": now}
json.dump(tracker, open(tracker_path, "w"))
PY
)
  if echo "$LOOP_CHECK" | grep -q "LOOP_DETECTED"; then
    LOOP_COUNT=$(echo "$LOOP_CHECK" | sed 's/LOOP_DETECTED://')
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║           PRForge Loop Detector — CIRCUIT BROKEN            ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║  Transition $TRANSITION_KEY failed $LOOP_COUNT times."
    echo "║  The agent is stuck in a loop and cannot self-recover."
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "This is a HARD BLOCK. The agent cannot retry this transition."
    echo "Options:"
    echo "  1. Investigate the root cause: cat $HARNESS_DIR/redirects/current.json"
    echo "  2. Fix the underlying issue manually"
    echo "  3. Reset the loop tracker: rm $LOOP_TRACKER"
    echo "  4. If the redirect is wrong, update the phase contract and retry"
    echo ""
    # Reset the counter so the user can try again after fixing
    python3 - "$LOOP_TRACKER" "$TRANSITION_KEY" <<'PY'
import json, sys
from pathlib import Path
tracker_path = Path(sys.argv[1])
key = sys.argv[2]
tracker = {}
if tracker_path.exists():
    try:
        tracker = json.load(open(tracker_path))
    except:
        pass
tracker.pop(key, None)
json.dump(tracker, open(tracker_path, "w"))
PY
    exit 1
  fi
fi

for allowed in "${ALLOWED_TRANSITIONS[@]}"; do
  if [ "$allowed" = "$TRANSITION" ]; then
    # Reset loop counter on successful transition
    if [ -f "$LOOP_TRACKER" ] && command -v python3 >/dev/null 2>&1; then
      python3 - "$LOOP_TRACKER" "$TRANSITION_KEY" <<'PY'
import json, sys
from pathlib import Path
tracker_path = Path(sys.argv[1])
key = sys.argv[2]
tracker = {}
if tracker_path.exists():
    try:
        tracker = json.load(open(tracker_path))
    except:
        pass
tracker.pop(key, None)
json.dump(tracker, open(tracker_path, "w"))
PY
    fi
    exit 0
  fi
done

# --- Illegal transition — redirect ---
REPO_ROOT=$(git -C "$(dirname "$FILE_PATH")" rev-parse --show-toplevel 2>/dev/null || pwd)
HARNESS_DIR=$(prforge_artifact_dir "$REPO_ROOT")
OBJECTIVE=$(python3 -c "
import json
try:
    d = json.load(open('$FILE_PATH'))
    print(d.get('task', {}).get('objective', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")
prforge_write_redirect "$REPO_ROOT" "$HARNESS_DIR" "illegal_phase_transition" "${CURRENT_PHASE}->${NEW_PHASE}" "$FILE_PATH" "$CURRENT_PHASE" "enter the required intermediate or repair phase" "$CURRENT_PHASE" "$OBJECTIVE" || true

echo ""
echo "=== PRForge Phase Boundary Violation ==="
echo "REDIRECTED: Cannot advance from $CURRENT_PHASE → $NEW_PHASE"
echo ""

case "$CURRENT_PHASE:$NEW_PHASE" in
  IMPLEMENT:SELF_REVIEW|IMPLEMENT:PACKAGE|IMPLEMENT:APPROVAL|IMPLEMENT:COMPLETE*)
    echo "You are in IMPLEMENT. You must advance through all intermediate phases:"
    echo ""
    echo "  IMPLEMENT → VALIDATE → SELF_REVIEW → PACKAGE → APPROVAL → POSTMORTEM → MEMORY_INDEX → COMPLETE"
    echo ""
    echo "Next required phase: VALIDATE"
    echo "Action: Run the validation commands in your contract.md, write"
    echo "        validation_ledger.md, then set phase = VALIDATE."
    ;;
  VALIDATE:PACKAGE|VALIDATE:APPROVAL|VALIDATE:COMPLETE*)
    echo "You are in VALIDATE. You must complete SELF_REVIEW before packaging:"
    echo ""
    echo "  VALIDATE → SELF_REVIEW → PACKAGE → APPROVAL → POSTMORTEM → MEMORY_INDEX → COMPLETE"
    echo ""
    echo "Next required phase: SELF_REVIEW"
    echo "Action: Load and execute the self_review.md playbook. Answer all 10"
    echo "        audit questions. Write hostile_review.md. Then set phase = SELF_REVIEW."
    ;;
  SELF_REVIEW:APPROVAL|SELF_REVIEW:COMPLETE*)
    echo "You are in SELF_REVIEW. Packaging artifacts must be generated first:"
    echo ""
    echo "  SELF_REVIEW → PACKAGE → APPROVAL → POSTMORTEM → MEMORY_INDEX → COMPLETE"
    echo ""
    echo "Next required phase: PACKAGE"
    echo "Action: Load and execute the package.md playbook. Generate PR body,"
    echo "        compute hashes, write approval.md. Then set phase = PACKAGE."
    ;;
  PACKAGE:COMPLETE|PACKAGE:APPROVAL)
    # PACKAGE → APPROVAL is valid but listed as correction here just in case
    if [ "$NEW_PHASE" != "APPROVAL" ]; then
      echo "You are in PACKAGE. You must present the approval artifact to the user"
      echo "and receive explicit confirmation before shipping:"
      echo ""
      echo "  PACKAGE → APPROVAL → POSTMORTEM → MEMORY_INDEX → COMPLETE"
      echo ""
      echo "Next required phase: APPROVAL"
      echo "Action: Present the full approval artifact from approval.md to the user."
      echo "        Ask the explicit approval question naming branch/remote/action."
      echo "        Wait for an affirmative response before setting phase = COMPLETE."
    fi
    ;;
  *)
    echo "This transition is not in the allowed table."
    echo ""
    echo "Valid transitions from $CURRENT_PHASE:"
    FOUND=0
    for t in "${ALLOWED_TRANSITIONS[@]}"; do
      if echo "$t" | grep -q "^${CURRENT_PHASE}:"; then
        echo "  → ${t#*:}"
        FOUND=1
      fi
    done
    if [ "$FOUND" = "0" ]; then
      echo "  (none — $CURRENT_PHASE may be a terminal phase)"
    fi
    echo ""
    echo "Corrective loops allowed: VALIDATE→IMPLEMENT, SELF_REVIEW→IMPLEMENT,"
    echo "  PACKAGE→INVESTIGATE, APPROVAL→PACKAGE, APPROVAL→INVESTIGATE"
    ;;
esac

echo ""
prforge_redirect_message "${CURRENT_PHASE}->${NEW_PHASE}" "This phase transition skips required work or bypasses a repair loop." "current safe phase work, reads, validation, repair-state updates" "resolve $HARNESS_DIR/redirects/current.json, then advance through the next valid phase" "$CURRENT_PHASE"
exit 1
