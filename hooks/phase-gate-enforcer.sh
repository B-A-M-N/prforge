#!/bin/bash
# PRForge Phase Gate Enforcer
# Fires as PreToolUse on Bash — checks whether the requested tool/action
# is allowed in the current phase. This is the "gate-scoped autonomy" model:
#   Approve gate → agent can do approved things until next gate
#   Out-of-envelope actions get redirected, not killed
#
# Allowed actions pass silently (exit 0).
# Blocked actions return a redirect message (exit 1).

set +e  # Never crash — blocking is done via exit 1 + stderr message

HOOK_JSON=$(cat)

# Parse tool name
TOOL_NAME=""
if command -v jq >/dev/null 2>&1; then
  TOOL_NAME=$(echo "$HOOK_JSON" | jq -r '.tool_name // empty' 2>/dev/null || echo "")
elif command -v python3 >/dev/null 2>&1; then
  TOOL_NAME=$(echo "$HOOK_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null || echo "")
fi

# Only enforce for Bash tool (where dangerous actions happen)
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Parse the command
CMD=""
if command -v jq >/dev/null 2>&1; then
  CMD=$(echo "$HOOK_JSON" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
elif command -v python3 >/dev/null 2>&1; then
  CMD=$(echo "$HOOK_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")
fi

# Find state.json
STATE_FILE=""
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PRFORGE_ROOT="${PRFORGE_HOME:-$HOME/.prforge}"

# Check common locations
for candidate in \
  "$REPO_ROOT/.prforge/state.json" \
  "$PRFORGE_ROOT/runs/"*/state.json \
  "$PRFORGE_ROOT/state.json"; do
  if [ -f "$candidate" ]; then
    STATE_FILE="$candidate"
    break
  fi
done

if [ -z "$STATE_FILE" ]; then
  exit 0  # No active PRForge run — don't enforce
fi

# Read current phase
CURRENT_PHASE=""
if command -v python3 >/dev/null 2>&1; then
  CURRENT_PHASE=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('phase', ''))
except:
    print('')
" 2>/dev/null || echo "")
fi

if [ -z "$CURRENT_PHASE" ]; then
  exit 0
fi

# Read allowed_actions and blocked_actions from state
ALLOWED_ACTIONS=""
BLOCKED_ACTIONS=""
if command -v python3 >/dev/null 2>&1; then
  ALLOWED_ACTIONS=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    scope = d.get('scope', {})
    acts = scope.get('allowed_actions', [])
    print(' '.join(acts))
except:
    print('')
" 2>/dev/null || echo "")
  BLOCKED_ACTIONS=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    scope = d.get('scope', {})
    acts = scope.get('blocked_actions', [])
    print(' '.join(acts))
except:
    print('')
" 2>/dev/null || echo "")
fi

# --- Phase-based tool restrictions ---
# These define what tool categories are allowed in each phase.
# The agent gets freedom within the envelope.

case "$CURRENT_PHASE" in
  INTAKE|CONTRACT|REPRODUCE|INVESTIGATE)
    # Read-only phases: no git write, no push, no PR creation
    if echo "$CMD" | grep -qE "^git (push|merge|rebase|reset|checkout -b|branch -D|tag)"; then
      echo "⛔ PRForge Gate: '$CURRENT_PHASE' does not permit git write operations." >&2
      echo "   Allowed: git log, git diff, git show, git status, git branch (read-only)" >&2
      echo "   Blocked: git push, git merge, git rebase, git reset, git checkout -b" >&2
      exit 1
    fi
    if echo "$CMD" | grep -qE "^gh (pr|issue|release|repo|run|workflow|secret|label|project|alias|auth|extension|gist|run)"; then
      # Allow gh pr view, gh issue view, gh api (read-only)
      if ! echo "$CMD" | grep -qE "^gh (pr view|issue view|api|auth status|repo view|run view|workflow view)"; then
        echo "⛔ PRForge Gate: '$CURRENT_PHASE' does not permit GitHub write operations." >&2
        echo "   Allowed: gh pr view, gh issue view, gh api (GET), gh auth status" >&2
        echo "   Blocked: gh pr create, gh pr comment, gh issue create, gh label, etc." >&2
        exit 1
      fi
    fi
    ;;

  IMPLEMENT)
    # Edit phase: can edit files, run tests, git commit locally
    # Cannot push, create PR, post comments
    if echo "$CMD" | grep -qE "^git push"; then
      echo "⛔ PRForge Gate: 'IMPLEMENT' does not permit git push." >&2
      echo "   Push is only permitted from APPROVAL phase after user approval." >&2
      exit 1
    fi
    if echo "$CMD" | grep -qE "^gh pr (create|comment|edit|close|merge|review)"; then
      echo "⛔ PRForge Gate: 'IMPLEMENT' does not permit PR operations." >&2
      echo "   PR operations are only permitted from APPROVAL phase." >&2
      exit 1
    fi
    ;;

  VALIDATE)
    # Validation phase: can run tests, read results
    # No git write, no push, no PR
    if echo "$CMD" | grep -qE "^git (push|commit|merge|rebase|reset)"; then
      echo "⛔ PRForge Gate: 'VALIDATE' does not permit git write operations." >&2
      echo "   If tests fail, transition back to IMPLEMENT to fix." >&2
      exit 1
    fi
    if echo "$CMD" | grep -qE "^gh pr"; then
      echo "⛔ PRForge Gate: 'VALIDATE' does not permit PR operations." >&2
      exit 1
    fi
    ;;

  SELF_REVIEW|PACKAGE)
    # Packaging phases: read-only, generate artifacts
    if echo "$CMD" | grep -qE "^git (push|commit|merge|rebase|reset|checkout)"; then
      echo "⛔ PRForge Gate: '$CURRENT_PHASE' does not permit git write operations." >&2
      exit 1
    fi
    if echo "$CMD" | grep -qE "^gh pr (create|comment|edit|close|merge|review)"; then
      echo "⛔ PRForge Gate: '$CURRENT_PHASE' does not permit PR operations." >&2
      echo "   PR operations are only permitted from APPROVAL phase after user approval." >&2
      exit 1
    fi
    ;;

  APPROVAL)
    # Approval phase: waiting for user decision
    # Only allow read operations and the specific approved actions
    if echo "$CMD" | grep -qE "^git push"; then
      # Check if push is in approved_actions
      if ! echo "$ALLOWED_ACTIONS" | grep -q "push"; then
        echo "⛔ PRForge Gate: 'APPROVAL' — git push not in approved_actions." >&2
        echo "   Wait for user approval and use /pr-approve to execute." >&2
        exit 1
      fi
    fi
    if echo "$CMD" | grep -qE "^gh pr create"; then
      if ! echo "$ALLOWED_ACTIONS" | grep -q "create_pr"; then
        echo "⛔ PRForge Gate: 'APPROVAL' — gh pr create not in approved_actions." >&2
        echo "   Wait for user approval and use /pr-approve to execute." >&2
        exit 1
      fi
    fi
    ;;

  POSTMORTEM|MEMORY_INDEX|COMPLETE)
    # Memory phases: read-only, generate memory artifacts
    if echo "$CMD" | grep -qE "^git (push|commit|merge|rebase|reset|checkout|branch)"; then
      echo "⛔ PRForge Gate: '$CURRENT_PHASE' — no git write operations in memory phases." >&2
      exit 1
    fi
    if echo "$CMD" | grep -qE "^gh pr"; then
      echo "⛔ PRForge Gate: '$CURRENT_PHASE' — no PR operations in memory phases." >&2
      exit 1
    fi
    ;;

  BLOCKED|ABORTED)
    # Blocked: only allow read operations and state inspection
    if echo "$CMD" | grep -qE "^git (push|commit|merge|rebase|reset|checkout|branch|tag)"; then
      echo "⛔ PRForge Gate: Run is BLOCKED/ABORTED. Resolve blocker before proceeding." >&2
      exit 1
    fi
    if echo "$CMD" | grep -qE "^gh pr"; then
      echo "⛔ PRForge Gate: Run is BLOCKED/ABORTED. Resolve blocker before proceeding." >&2
      exit 1
    fi
    ;;

  *)
    # Unknown phase — don't enforce
    exit 0
    ;;
esac

# Check blocked_actions from state (explicit denylist)
if [ -n "$BLOCKED_ACTIONS" ]; then
  for blocked in $BLOCKED_ACTIONS; do
    if echo "$CMD" | grep -q "$blocked"; then
      echo "⛔ PRForge Gate: Action blocked by contract: '$blocked'" >&2
      echo "   This action is in the blocked_actions list for the current run." >&2
      exit 1
    fi
  done
fi

exit 0
