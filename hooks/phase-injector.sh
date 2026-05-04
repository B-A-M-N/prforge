#!/bin/bash
# PRForge Phase Playbook Injector
# Fires as PostToolUse on Write — after a successful state.json write.
# Reads the new phase and outputs a mandatory reminder to load the phase playbook.
# Advisory only: exits 0 always. Output is injected into the model's context.

# Don't let errors kill this hook — it's advisory
set +e

HOOK_JSON=$(cat)

# --- Parse file_path ---
FILE_PATH=""
if command -v jq >/dev/null 2>&1; then
  FILE_PATH=$(echo "$HOOK_JSON" | jq -r '.tool_input.file_path // empty' 2>/dev/null || echo "")
elif command -v python3 >/dev/null 2>&1; then
  FILE_PATH=$(echo "$HOOK_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null || echo "")
fi

# Only act on PRForge state.json writes. State may be in
# ${PRFORGE_HOME:-~/.prforge}/runs/... or legacy repo/.prforge.
case "$FILE_PATH" in
  */state.json) ;;
  *) exit 0 ;;
esac
PRFORGE_ROOT="${PRFORGE_HOME:-$HOME/.prforge}"
case "$FILE_PATH" in
  "$PRFORGE_ROOT"/*|*/.prforge/state.json|*/.prforge/runs/*/state.json) ;;
  *) exit 0 ;;
esac

# --- Read new phase from the file that was just written ---
NEW_PHASE=""
if [ -f "$FILE_PATH" ] && command -v python3 >/dev/null 2>&1; then
  NEW_PHASE=$(python3 -c "
import json
try:
    d = json.load(open('$FILE_PATH'))
    print(d.get('phase', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")
fi

if [ -z "$NEW_PHASE" ]; then
  exit 0
fi

# --- Map phase to playbook filename ---
case "$NEW_PHASE" in
  INTAKE)              PLAYBOOK="intake.md" ;;
  INVESTIGATE)         PLAYBOOK="investigate.md" ;;
  PLAN)                PLAYBOOK="plan.md" ;;
  IMPLEMENT)           PLAYBOOK="implement.md" ;;
  VALIDATE)            PLAYBOOK="validate.md" ;;
  SELF_REVIEW)         PLAYBOOK="self_review.md" ;;
  PACKAGE)             PLAYBOOK="package.md" ;;
  APPROVAL)            PLAYBOOK="approval.md" ;;
  SHIPPED|SHIPPED_PENDING) PLAYBOOK="shipped.md" ;;
  BLOCKED)             PLAYBOOK="blocked.md" ;;
  SCOPE_RECONCILE|STATE_SYNC_REPAIR|LEASE_RENEWAL_REPAIR|REVIEW_REFRESH|CONTRACT_UPDATE|PLAN_UPDATE|VALIDATION_REPAIR|INTELLIGENCE_REPAIR|ARTIFACT_REPAIR|COORDINATOR_RECONCILE)
    PLAYBOOK="blocked.md"
    ;;
  *)
    exit 0
    ;;
esac

# --- Discover skill root ---
SKILL_ROOT=""

# 1. Try CLAUDE_PLUGIN_ROOT env var (set by plugin system)
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "${CLAUDE_PLUGIN_ROOT}/skills/prforge/phases/${PLAYBOOK}" ]; then
  SKILL_ROOT="${CLAUDE_PLUGIN_ROOT}/skills/prforge"
fi

# 2. Try to find via filesystem search
if [ -z "$SKILL_ROOT" ]; then
  FOUND=$(find "$HOME" -path "*/skills/prforge/phases/${PLAYBOOK}" -type f 2>/dev/null | head -1)
  if [ -n "$FOUND" ]; then
    SKILL_ROOT=$(dirname "$FOUND")
    SKILL_ROOT=$(dirname "$SKILL_ROOT")  # go up from phases/ to skills/prforge/
  fi
fi

# --- Output injection ---
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           PRForge Phase Injector — ACTION REQUIRED           ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Phase advanced to: $NEW_PHASE"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

if [ -n "$SKILL_ROOT" ] && [ -f "$SKILL_ROOT/phases/$PLAYBOOK" ]; then
  echo "MANDATORY: Read and fully execute the phase playbook before any other action."
  echo ""
  echo "  Playbook path: $SKILL_ROOT/phases/$PLAYBOOK"
  echo ""
  echo "The playbook contains required steps and a PHASE EXIT GATE checklist."
  echo "Every item in the exit gate must be complete before advancing phases."
  echo "Do NOT proceed based on memory or intuition — read the playbook now."
else
  echo "MANDATORY: Find and read the phase playbook for $NEW_PHASE before proceeding."
  echo ""
  echo "  Run this to locate it:"
  echo "  find \"\$HOME\" -path \"*/skills/prforge/phases/$PLAYBOOK\" -type f | head -1"
  echo ""
  echo "Then read that file completely before taking any further action."
  echo "The playbook contains required steps and a PHASE EXIT GATE checklist."
fi

echo ""
echo "Phase-specific reminders for $NEW_PHASE:"
case "$NEW_PHASE" in
  INVESTIGATE)
    echo "  • Probe GitNexus: mcp__gitnexus__list_repos({})"
    echo "  • Probe context-mode: ctx_search with a trivial query"
    echo "  • Call ALL mandatory intelligence tools for your detected tier"
    echo "  • Write repo_intelligence.md before advancing to PLAN"
    ;;
  VALIDATE)
    echo "  • Run EVERY command listed in contract.md validation_commands"
    echo "  • Record honest results in validation_ledger.md"
    echo "  • NEVER claim validation passed without running the command"
    echo "  • No git write operations after this phase"
    ;;
  SELF_REVIEW)
    echo "  • Answer all 10 hostile audit questions"
    echo "  • Verify PRForge artifact paths are NOT in git index before advancing"
    echo "  • Question #9: confirm branch is NOT main/master"
    echo "  • Question #10: confirm output language is English"
    ;;
  PACKAGE)
    echo "  • Run all 10 approval guards before generating approval.md"
    echo "  • Compute diff_hash and validation_hash — store in state.json"
    echo "  • Do NOT push, post, or create PRs after this phase"
    ;;
  APPROVAL)
    echo "  • Present the FULL approval artifact from approval.md"
    echo "  • Ask the explicit closing question: name branch/remote/action"
    echo "  • User silence is NOT approval — wait for affirmative response"
    echo "  • 'Looks good', 'yes', 'go ahead', 'push it' all count"
    ;;
  SHIPPED|SHIPPED_PENDING)
    echo "  • Verify idempotency guard FIRST: consumed != true"
    echo "  • Verify diff_hash and validation_hash still match"
    echo "  • Execute ONLY actions in state.approval.approved_actions"
    echo "  • Append to shipping_ledger.json after each public action"
    echo "  • Set approval.consumed = true when done"
    ;;
  SCOPE_RECONCILE|STATE_SYNC_REPAIR|LEASE_RENEWAL_REPAIR|REVIEW_REFRESH|CONTRACT_UPDATE|PLAN_UPDATE|VALIDATION_REPAIR|INTELLIGENCE_REPAIR|ARTIFACT_REPAIR|COORDINATOR_RECONCILE)
    echo "  • This is a recoverable redirect, not task completion"
    echo "  • Read redirects/current.json and perform the required next action"
    echo "  • Keep the original objective pinned"
    echo "  • Return to the prior phase after repair"
    if [ "$NEW_PHASE" = "INTELLIGENCE_REPAIR" ]; then
      echo "  • Probe GitNexus and record intelligence.evidence before PLAN"
      echo "  • If GitNexus is unavailable, document unavailable_reason and fallback_commands"
    fi
    ;;
esac

echo ""
exit 0
