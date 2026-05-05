#!/bin/bash
# PRForge Phase Boundary Enforcer
# Fires as PreToolUse on Write — intercepts state.json writes and validates
# that the requested phase transition is in the allowed table.
# Exits 1 (blocking) on illegal jumps. Exits 0 otherwise.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hooks/prforge-common.sh
. "$SCRIPT_DIR/prforge-common.sh"

# Diagnostic: log hook invocation (minimal, for validation only)
mkdir -p "$(git rev-parse --show-toplevel 2>/dev/null)/.prforge" 2>/dev/null
echo "$(date -Iseconds) [phase-boundary] Write hook fired" >> "$(git rev-parse --show-toplevel 2>/dev/null)/.prforge/hook_events.log" 2>/dev/null || true

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

# --- Parse new phase from content being written ---
CONTENT=""
if command -v jq >/dev/null 2>&1; then
  CONTENT=$(echo "$HOOK_JSON" | jq -r '.tool_input.content // empty' 2>/dev/null || echo "")
elif command -v python3 >/dev/null 2>&1; then
  CONTENT=$(echo "$HOOK_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('content',''))" 2>/dev/null || echo "")
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

# If we can't parse the new phase, allow the write (don't block on parse failures)
if [ -z "$NEW_PHASE" ]; then
  exit 0
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
  "CONTRACT_UPDATE"
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
  "CONTRACT_UPDATE:PLAN"
  "CONTRACT_UPDATE:IMPLEMENT"
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
  "COMPLETE:BLOCKED"
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

if intel.get("gitnexus_probe_attempted") is not True and "list_repos" not in calls:
    missing.append("record gitnexus_probe_attempted=true or gitnexus_calls includes list_repos")

if "gitnexus_available" not in intel:
    missing.append("record gitnexus_available=true/false")
elif intel.get("gitnexus_available") is True:
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
  VALIDATE_CHECK=$(PRFORGE_STATE_CONTENT="$CONTENT" python3 - "$HARNESS_DIR" <<'PY'
import json, os, sys
from pathlib import Path

artifact_dir = Path(sys.argv[1])
try:
    d = json.loads(os.environ.get("PRFORGE_STATE_CONTENT", ""))
    validation = d.get("validation", {})
    run = validation.get("commands_run", [])
    not_run = validation.get("commands_not_run", [])
    missing = []

    if not run:
        missing.append("validation.commands_run is empty — run every command listed in contract.md before self-review")
    else:
        failed = [c.get("command", "?") for c in run if c.get("status") != "passed"]
        if failed:
            missing.append("failing validations not resolved: " + ", ".join(failed[:3]))

    if not_run:
        missing.append("commands not yet run: " + ", ".join(c.get("command","?") for c in not_run[:3]))

    ledger = artifact_dir / "validation_ledger.md"
    if not ledger.exists():
        missing.append("validation_ledger.md not written — record all results (pass/fail/output) in the ledger")
    elif len(ledger.read_text(errors="replace").strip()) < 50:
        missing.append("validation_ledger.md is empty — fill in actual command results")

    print("FAIL:" + " | ".join(missing) if missing else "OK")
except Exception as e:
    print(f"FAIL:could not read state: {e}")
PY
)
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

for allowed in "${ALLOWED_TRANSITIONS[@]}"; do
  if [ "$allowed" = "$TRANSITION" ]; then
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
