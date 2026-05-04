#!/bin/bash
# PRForge Preflight Safety Check
# Runs deterministically before any upstream-facing or destructive action.
# Reads Claude Code tool invocation from stdin.
# Exits 0 for safe, 1 for unsafe (with reasons printed to stdout).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hooks/prforge-common.sh
. "$SCRIPT_DIR/prforge-common.sh"

# Diagnostic: log hook invocation (minimal, for validation only)
mkdir -p "$(git rev-parse --show-toplevel 2>/dev/null)/.prforge" 2>/dev/null
echo "$(date -Iseconds) [preflight] Bash hook fired" >> "$(git rev-parse --show-toplevel 2>/dev/null)/.prforge/hook_events.log" 2>/dev/null || true

# --- Read Hook Input ---
HOOK_JSON=$(cat)

# Parse the command from tool_input.command
CMD=""
if command -v jq >/dev/null 2>&1; then
  CMD=$(echo "$HOOK_JSON" | jq -r '.tool_input.command // empty')
else
  CMD=$(echo "$HOOK_JSON" | grep -o '"command": *"[^"]*"' | sed 's/"command": *"//;s/"$//' | head -1 || echo "")
fi

# If the command isn't related to git/gh upstream actions, exit immediately
PUBLIC_ACTION_RE='(^|[[:space:];|&])git[[:space:]]+(push|cherry-pick|reset)(^|[[:space:]])|(^|[[:space:];|&])gh[[:space:]]+pr[[:space:]]+(create|edit|merge|close|comment|review|ready|reopen)|(^|[[:space:];|&])gh[[:space:]]+issue[[:space:]]+(close|comment|edit)|(^|[[:space:];|&])gh[[:space:]]+api.*(comments|reviews|pulls|issues|merges)'
GIT_COMMIT_RE='(^|[[:space:];|&])git[[:space:]]+commit([[:space:]]|$)'
IS_PUBLIC_ACTION=false
IS_COMMIT=false
echo "$CMD" | grep -qiE "$PUBLIC_ACTION_RE" && IS_PUBLIC_ACTION=true || true
echo "$CMD" | grep -qiE "$GIT_COMMIT_RE"    && IS_COMMIT=true        || true

# --- Mesh Health Check (once per session, quiet auto-heal) ---
if [ -f "$HOME/.prforge-mesh/sessions/local/$(prforge_get_session_id 2>/dev/null || echo unknown)/node_id" ] ||
   [ -f "$HOME/.prforge-mesh/sessions/lan/$(prforge_get_session_id 2>/dev/null || echo unknown)/node_id" ]; then

  CHECK_FILE="$HOME/.prforge-mesh/.last_mesh_check"
  RUN_CHECK=true
  if [ -f "$CHECK_FILE" ]; then
    LAST_CHECK=$(cat "$CHECK_FILE")
    NOW=$(date +%s)
    if [ $((NOW - LAST_CHECK)) -lt 3600 ]; then
      RUN_CHECK=false
    fi
  fi

  if [ "$RUN_CHECK" = true ]; then
    MESH_SCRIPTS=$(find "$HOME" -path "*/prforge/*/scripts/mesh" -type d 2>/dev/null | head -1)
    if [ -n "$MESH_SCRIPTS" ]; then
      SID=$(prforge_get_session_id 2>/dev/null || echo "")
      if [ -n "$SID" ]; then
        if ! python3 "$MESH_SCRIPTS/meshctl.py" health --session "$SID" 2>/dev/null; then
          if python3 "$MESH_SCRIPTS/meshctl.py" heal --session "$SID" 2>/dev/null; then
            echo "Mesh was stale. Restarted node. Continuing."
          fi
        fi
      fi
    fi
    date +%s > "$CHECK_FILE"
  fi
fi

if [ "$IS_PUBLIC_ACTION" = "false" ] && [ "$IS_COMMIT" = "false" ]; then
  exit 0
fi

# --- Git commit phase gate ---
# Commits in INTAKE/INVESTIGATE/PLAN mean implementation has not begun.
# Redirect back with specific guidance rather than letting the commit through.
if [ "$IS_COMMIT" = "true" ]; then
  if git rev-parse --git-dir >/dev/null 2>&1; then
    COMMIT_REPO=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
    if [ -n "$COMMIT_REPO" ]; then
      COMMIT_HARNESS=$(prforge_artifact_dir "$COMMIT_REPO")
      COMMIT_STATE="$COMMIT_HARNESS/state.json"
      if [ -f "$COMMIT_STATE" ]; then
        COMMIT_PHASE=$(python3 -c "
import json
try:
    print(json.load(open('$COMMIT_STATE')).get('phase',''))
except:
    print('')
" 2>/dev/null || echo "")
        case "$COMMIT_PHASE" in
          INTAKE|INVESTIGATE|PLAN)
            COMMIT_OBJ=$(python3 -c "
import json
try:
    print(json.load(open('$COMMIT_STATE')).get('task',{}).get('objective','unknown'))
except:
    print('unknown')
" 2>/dev/null || echo "unknown")
            prforge_write_redirect "$COMMIT_REPO" "$COMMIT_HARNESS" \
              "premature_commit" "git commit" "working tree" "$COMMIT_PHASE" \
              "finish planning, write contract.md, transition to IMPLEMENT phase, then commit" \
              "$COMMIT_PHASE" "$COMMIT_OBJ" || true
            echo ""
            echo "=== PRForge Phase Gate: Commit Redirected ==="
            prforge_redirect_message "git commit" \
              "Phase is $COMMIT_PHASE — implementation not yet started. Commits belong in IMPLEMENT phase." \
              "reads, investigation, planning docs, contract.md, state.json phase update (PLAN then IMPLEMENT)" \
              "complete planning: write contract.md with scope/validation plan; set phase=IMPLEMENT; then commit" \
              "$COMMIT_PHASE"
            exit 1
            ;;
        esac
      fi
    fi
  fi
  # Commit in IMPLEMENT or later — allowed; skip public-action guards unless both apply
  [ "$IS_PUBLIC_ACTION" = "false" ] && exit 0
fi

PASS=0
FAIL=1
ISSUES=()
WARNINGS=()
ARTIFACT_RE='(^|/)\.prforge(/|$)|(^|/)\.prforge-run$|(^|/)\.prforge-[^/]+'
PHASE="not active"
ORIGINAL_OBJECTIVE="unknown"

# --- Git availability ---
if ! git rev-parse --git-dir > /dev/null 2>&1; then
  echo "DO NOT PUSH"
  echo "Reason: Not in a git repository."
  exit $FAIL
fi

# --- Working tree status ---
DIRTY=$(git status --porcelain 2>/dev/null | wc -l)
if [ "$DIRTY" -gt 0 ]; then
  NON_HARNESS=$(git status --porcelain 2>/dev/null | grep -vcE '^.{2} \.prforge(/|$)|^.{2} \.prforge-run$|^.{2} \.prforge-[^/]+') || true
  if [ "$NON_HARNESS" -gt 0 ]; then
    ISSUES+=("Working tree has $NON_HARNESS uncommitted change(s) outside PRForge artifacts")
    echo "Dirty files:"
    git status --porcelain | grep -vE '^.{2} \.prforge(/|$)|^.{2} \.prforge-run$|^.{2} \.prforge-[^/]+' | head -20
  else
    WARNINGS+=("PRForge artifact pointer/files have uncommitted changes")
  fi
fi

# --- Branch tracking ---
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ "$CURRENT_BRANCH" = "HEAD" ]; then
  ISSUES+=("Detached HEAD state — cannot safely push a branch")
fi

# --- Remote check ---
REMOTES=$(git remote 2>/dev/null || echo "")
if [ -z "$REMOTES" ]; then
  ISSUES+=("No remotes configured")
fi

# --- Upstream tracking ---
if [ -n "$CURRENT_BRANCH" ] && [ "$CURRENT_BRANCH" != "HEAD" ]; then
  TRACKING=$(git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}" 2>/dev/null || echo "")
  if [ -z "$TRACKING" ]; then
    WARNINGS+=("Branch '$CURRENT_BRANCH' does not track a remote branch")
  fi
fi

# --- Push target safety: warn if pushing to upstream instead of fork ---
if echo "$CMD" | grep -qiE 'git push'; then
  TARGET_REMOTE=$(echo "$CMD" | grep -oE 'origin|upstream' | head -1 || echo "")
  if [ "$TARGET_REMOTE" = "upstream" ]; then
    ISSUES+=("Pushing directly to 'upstream' remote — should push to 'origin' (fork) instead")
  fi
fi

# --- PRForge state ---
REPO_ROOT=$(git rev-parse --show-toplevel)
HARNESS_DIR=$(prforge_artifact_dir "$REPO_ROOT")
if ! prforge_ensure_pointer "$REPO_ROOT" "$HARNESS_DIR"; then
  ISSUES+=("Repo-local PRForge state must be a plain pointer file, not a symlink")
fi
STATE_FILE="$HARNESS_DIR/state.json"
APPROVAL_FILE="$HARNESS_DIR/approval.md"

# Check for approval artifact if state shows APPROVAL phase
if [ -f "$STATE_FILE" ]; then
  # Use prforge_state.py if available to read with lock, otherwise fallback
  if STATE_SCRIPT=$(prforge_state_py); then
    PHASE=$(python3 "$STATE_SCRIPT" read "$STATE_FILE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('phase', ''))" 2>/dev/null || echo "UNKNOWN")
  else
    prforge_lock_state "$STATE_FILE"
    PHASE=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('phase', 'UNKNOWN'))
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")
    prforge_unlock_state "$STATE_FILE"
  fi
  ORIGINAL_OBJECTIVE=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('task', {}).get('objective', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")

  case "$PHASE" in
    INTAKE|INVESTIGATE|PLAN|IMPLEMENT)
      ISSUES+=("PRForge is in phase $PHASE — implementation not yet complete")
      ;;
    VALIDATE)
      WARNINGS+=("PRForge is in VALIDATE — self-review and packaging not yet done")
      ;;
    SELF_REVIEW)
      WARNINGS+=("PRForge is in SELF_REVIEW — validation and packaging not yet done")
      ;;
    PACKAGE)
      WARNINGS+=("PRForge is in PACKAGE — approval artifact not yet generated")
      ;;
    APPROVAL)
      if [ ! -f "$APPROVAL_FILE" ]; then
        ISSUES+=("PRForge is in APPROVAL phase but no approval.md found")
      else
        # --- Stale approval check ---
        # If approval.stale is true, block push even in APPROVAL phase
        APPROVAL_STALE=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    approval = d.get('approval', {})
    if approval.get('stale', False):
        print('STALE')
    else:
        print('FRESH')
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

        if [ "$APPROVAL_STALE" = "STALE" ]; then
          ISSUES+=("Approval is STALE — code or validation changed after approval.md was generated. Regenerate approval first.")
        elif [ "$APPROVAL_STALE" = "UNKNOWN" ]; then
          WARNINGS+=("Could not determine approval staleness — verify manually")
        fi

        # --- Diff hash verification ---
        # Compare current diff --stat hash against approval.diff_hash
        if command -v sha256sum >/dev/null 2>&1; then
          STORED_HASH=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('approval', {}).get('diff_hash', ''))
except:
    print('')
" 2>/dev/null || echo "")

          if [ -n "$STORED_HASH" ]; then
            CURRENT_HASH=$(git diff --stat 2>/dev/null | sha256sum | awk '{print $1}')
            CURRENT_HASH="$CURRENT_HASH$(git diff --cached --stat 2>/dev/null | sha256sum | awk '{print $1}')"
            if [ "$CURRENT_HASH" != "$STORED_HASH" ]; then
              ISSUES+=("Diff hash mismatch — code changed since approval was generated. Approval is stale.")
            fi
          fi
        fi
      fi

      # --- Guard #4: Artifact exclusion check ---
      STAGED_ARTIFACTS=$(git diff --cached --name-only 2>/dev/null | grep -Ec "$ARTIFACT_RE" || true)
      TRACKED_ARTIFACTS=$(git ls-files 2>/dev/null | grep -Ec "$ARTIFACT_RE" || true)
      if [ "$STAGED_ARTIFACTS" -gt 0 ]; then
        ISSUES+=("PRForge artifacts or pointer are staged for commit — unstage them before pushing")
      fi
      if [ "$TRACKED_ARTIFACTS" -gt 0 ] && [ "$STAGED_ARTIFACTS" -eq 0 ]; then
        WARNINGS+=("PRForge artifacts or pointer are tracked in git — they should only be in .git/info/exclude")
      fi

      # --- Guard #6: Commit hygiene check ---
      # Resolve base ref dynamically: prefer upstream/main → upstream/master → origin/main → origin/master
      BASE_REF=""
      for candidate in upstream/main upstream/master origin/main origin/master; do
        git rev-parse --verify "$candidate" >/dev/null 2>&1 && BASE_REF="$candidate" && break
      done

      if command -v grep >/dev/null 2>&1 && [ -n "$BASE_REF" ]; then
        HYGIENE_VIOLATIONS=$(git log --format="%s%n%b" "${BASE_REF}..HEAD" 2>/dev/null | grep -ciE '(^WIP|^debug|^temp|^fixup|^squash|Co-authored-by|Generated by|Generated with|Claude Code|Anthropic|ChatGPT|GPT-|Copilot)' || true)
        if [ "$HYGIENE_VIOLATIONS" -gt 0 ]; then
          VIOLATION_MESSAGES=$(git log --format="%h %s" "${BASE_REF}..HEAD" 2>/dev/null | grep -iE '(^WIP|^debug|^temp|^fixup|^squash|Co-authored-by|Generated by|Generated with|Claude Code|Anthropic|ChatGPT|GPT-|Copilot)' || true)
          ISSUES+=("Commit hygiene violations found — amend/squash before pushing:")
          while IFS= read -r v; do
            ISSUES+=("  ✗ $v")
          done <<< "$VIOLATION_MESSAGES"
        fi
      fi

      
      # --- Guard #11: Breaking Change / Semantic Versioning Check ---
      PUBLIC_API_TOUCHED=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print('true' if d.get('blast_radius', {}).get('public_api_touched', False) else 'false')
except:
    print('false')
" 2>/dev/null || echo "false")
      
      if [ "$PUBLIC_API_TOUCHED" = "true" ]; then
        BREAKING_DECLARED=false
        if [ -f "$HARNESS_DIR/pr_body.md" ] && grep -q "BREAKING CHANGE" "$HARNESS_DIR/pr_body.md"; then
          BREAKING_DECLARED=true
        fi
        if [ "$BREAKING_DECLARED" = "false" ] && [ -n "$BASE_REF" ]; then
          if git log --format="%s%n%b" "${BASE_REF}..HEAD" 2>/dev/null | grep -q "BREAKING CHANGE"; then
            BREAKING_DECLARED=true
          fi
        fi
        
        if [ "$BREAKING_DECLARED" = "false" ]; then
           ISSUES+=("Public API was modified but no BREAKING CHANGE footer was found in PR body or commits. Document breaking changes or bump major version.")
        fi
      fi

      # --- Guard #12: Commit Granularity Check ---
      if [ -n "$BASE_REF" ]; then
        LARGE_COMMITS=$(git log --format="%h" "${BASE_REF}..HEAD" 2>/dev/null | while read hash; do
          count=$(git diff-tree --no-commit-id --name-only -r "$hash" 2>/dev/null | wc -l)
          if [ "$count" -gt 10 ]; then
            echo "$hash ($count files)"
          fi
        done)
        if [ -n "$LARGE_COMMITS" ]; then
           ISSUES+=("Commit granularity violation — commits should be atomic. The following commits are too large (>10 files) and should be split:")
           while IFS= read -r lc; do
             ISSUES+=("  ✗ $lc")
           done <<< "$LARGE_COMMITS"
        fi
      fi

      # --- Guard #13: Linter/Formatter Check ---
      FORMAT_ISSUES=""
      if command -v prettier >/dev/null 2>&1 && [ -f "$REPO_ROOT/package.json" ]; then
         if ! npx prettier --check . >/dev/null 2>&1; then
             FORMAT_ISSUES="prettier formatting failed"
         fi
      elif command -v black >/dev/null 2>&1 && [ -f "$REPO_ROOT/pyproject.toml" ]; then
         if ! black --check . >/dev/null 2>&1; then
             FORMAT_ISSUES="black formatting failed"
         fi
      elif command -v cargo >/dev/null 2>&1 && [ -f "$REPO_ROOT/Cargo.toml" ]; then
         if ! cargo fmt -- --check >/dev/null 2>&1; then
             FORMAT_ISSUES="cargo fmt failed"
         fi
      fi
      if [ -n "$FORMAT_ISSUES" ]; then
         ISSUES+=("Code style violation: $FORMAT_ISSUES — run formatter before packaging/shipping (transition to STYLE_REPAIR)")
      fi

# --- Guard #8: Approval status check ---
      APPROVAL_STATUS=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('release', {}).get('approval_status', 'UNKNOWN'))
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

      if [ "$APPROVAL_STATUS" = "BLOCKED" ]; then
        BLOCKING_REASONS=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    reasons = d.get('release', {}).get('blocking_reasons', [])
    for r in reasons:
        print(r)
except:
    pass
" 2>/dev/null || echo "")
        ISSUES+=("Approval status is BLOCKED:")
        while IFS= read -r r; do
          [ -n "$r" ] && ISSUES+=("  ✗ $r")
        done <<< "$BLOCKING_REASONS"
      elif [ "$APPROVAL_STATUS" = "UNKNOWN" ]; then
        WARNINGS+=("Could not determine approval status from state.json")
      fi

      # --- Guard #1: Review freshness check ---
      REVIEW_FRESH=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    rf = d.get('review_freshness', {})
    if rf.get('fresh', False):
        print('FRESH')
    else:
        print('STALE')
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

      if [ "$REVIEW_FRESH" = "STALE" ]; then
        NEW_COMMENTS=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('review_freshness', {}).get('new_comments_since_fetch', 0))
except:
    print('?')
" 2>/dev/null || echo "?")
        ISSUES+=("Review is STALE — $NEW_COMMENTS new comment(s) since last fetch. Re-run INVESTIGATE.")
      elif [ "$REVIEW_FRESH" = "UNKNOWN" ]; then
        WARNINGS+=("Could not determine review freshness — verify manually")
      fi

      # --- Guard #2: CI/check status check ---
      CI_STATUS=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('ci_status', {}).get('overall', 'UNKNOWN'))
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

      if [ "$CI_STATUS" = "failed_related" ]; then
        FAILED_CHECKS=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    checks = d.get('ci_status', {}).get('failed_checks', [])
    for c in checks:
        if c.get('related_to_changes', False):
            print(f\"  - {c.get('name', 'unknown')}: {c.get('reason', 'no reason')}\")
except:
    pass
" 2>/dev/null || echo "")
        ISSUES+=("GitHub checks FAILED with changes-related errors:")
        while IFS= read -r c; do
          [ -n "$c" ] && ISSUES+=("$c")
        done <<< "$FAILED_CHECKS"
      elif [ "$CI_STATUS" = "pending" ]; then
        ISSUES+=("GitHub checks are still pending — must wait for green CI before shipping. Use gh pr checks --watch or transition to POLL_CI.")
      fi

      # --- Guard #3: Branch/base drift check ---
      DRIFT_STATUS=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('branch_status', {}).get('drift_status', 'UNKNOWN'))
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

      if [ "$DRIFT_STATUS" = "base_diverged_needs_rebase" ]; then
        ISSUES+=("Branch has diverged from upstream base — rebase required before pushing")
      elif [ "$DRIFT_STATUS" = "wrong_base_branch" ]; then
        ISSUES+=("Branch is based on wrong base branch — verify upstream/base branch")
      elif [ "$DRIFT_STATUS" = "base_behind_but_safe" ]; then
        WARNINGS+=("Branch is behind upstream base — consider rebasing before pushing")
      fi

      # --- Guard #7: Scope delta check ---
      SCOPE_CLEAN=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    dc = d.get('scope', {}).get('delta_check', {})
    if dc.get('scope_clean', False):
        print('CLEAN')
    else:
        print('DIRTY')
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

      if [ "$SCOPE_CLEAN" = "DIRTY" ]; then
        UNEXPECTED=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    files = d.get('scope', {}).get('delta_check', {}).get('unexpected_files', [])
    for f in files:
        print(f\"  - {f}\")
except:
    pass
" 2>/dev/null || echo "")
        ISSUES+=("Scope delta: unexpected files changed outside contract:")
        while IFS= read -r f; do
          [ -n "$f" ] && ISSUES+=("$f")
        done <<< "$UNEXPECTED"
      fi

      # --- Guard #9: Ownership ambiguity check ---
      OWNERSHIP_AMBIGUOUS=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    own = d.get('ownership', {})
    if own.get('ambiguous', False):
        print('AMBIGUOUS')
    else:
        print('CLEAR')
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

      if [ "$OWNERSHIP_AMBIGUOUS" = "AMBIGUOUS" ]; then
        OWNERSHIP_RES=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('ownership', {}).get('resolution', 'unknown'))
except:
    print('unknown')
" 2>/dev/null || echo "unknown")
        ISSUES+=("Ownership is ambiguous ($OWNERSHIP_RES) — confirm PR ownership before pushing")
      fi

      # --- Guard #10: GitNexus disclosure check ---
      GITNEXUS_AVAIL=$(python3 -c "
import json, sys
try:
    d = json.load(open('$STATE_FILE'))
    if d.get('intelligence', {}).get('gitnexus_available', True):
        print('AVAILABLE')
    else:
        print('UNAVAILABLE')
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

      if [ "$GITNEXUS_AVAIL" = "UNAVAILABLE" ]; then
        DISCLOSURE=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('intelligence', {}).get('disclosure', ''))
except:
    print('')
" 2>/dev/null || echo "")
        if [ -z "$DISCLOSURE" ]; then
          ISSUES+=("GitNexus unavailable but no disclosure text in state.json — add disclosure")
        fi
        UNCAP=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    caps = d.get('intelligence', {}).get('unavailable_capabilities', [])
    print(', '.join(caps) if caps else '')
except:
    print('')
" 2>/dev/null || echo "")
        if [ -z "$UNCAP" ]; then
          WARNINGS+=("GitNexus unavailable but no unavailable_capabilities listed — document what was missing")
        fi
      fi

      # --- Test coverage check (HARD GATE) ---
      TESTS_FOUND=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('blast_radius', {}).get('tests_found_count', 0))
except:
    print('0')
" 2>/dev/null || echo "0")

      CHANGED_COUNT=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('blast_radius', {}).get('changed_files_count', 0))
except:
    print('0')
" 2>/dev/null || echo "0")

      if [ "$TESTS_FOUND" = "0" ] && [ "$CHANGED_COUNT" -gt 0 ] 2>/dev/null; then
        ISSUES+=("No test coverage for $CHANGED_COUNT changed file(s) — add tests before pushing")
      fi

      # --- Validation completeness check (HARD GATE) ---
      VALIDATION_STATUS=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    validation = d.get('validation', {})
    run = validation.get('commands_run', [])
    not_run = validation.get('commands_not_run', [])
    failed = [c.get('command', 'unknown') for c in run if c.get('status') != 'passed']
    if not run:
        print('NO_COMMANDS_RUN')
    elif failed:
        print('FAILED:' + ' | '.join(failed))
    elif not_run:
        print('NOT_RUN:' + ' | '.join(c.get('command', 'unknown') for c in not_run))
    else:
        print('COMPLETE')
except Exception:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

      case "$VALIDATION_STATUS" in
        NO_COMMANDS_RUN)
          ISSUES+=("Validation incomplete — no validation.commands_run entries recorded")
          ;;
        FAILED:*)
          ISSUES+=("Validation incomplete — failed command(s): ${VALIDATION_STATUS#FAILED:}")
          ;;
        NOT_RUN:*)
          ISSUES+=("Validation incomplete — command(s) were not run: ${VALIDATION_STATUS#NOT_RUN:}")
          ;;
        UNKNOWN)
          WARNINGS+=("Could not determine validation completeness from state.json")
          ;;
      esac

      # Check for stale tests (test files exist but weren't updated)
      STALE_TESTS=$(python3 -c "
import json, subprocess
try:
    d = json.load(open('$STATE_FILE'))
    changed = d.get('blast_radius', {}).get('changed_files', d.get('scope', {}).get('delta_check', {}).get('actual_changed_files', []))
    stale = []
    for f in changed:
        if f.endswith(('.test.', '.spec.')):
            continue
        import os
        base = os.path.basename(f).rsplit('.', 1)[0]
        dirn = os.path.dirname(f)
        # Find corresponding test
        import glob
        patterns = [f'{dirn}/**/{base}.test.*', f'{dirn}/**/{base}.spec.*', f'{dirn}/**/test_{base}.*']
        for p in patterns:
            for tf in glob.glob(p, recursive=True):
                # Check if test file was also changed
                import shutil
                base_ref = next((r for r in ['upstream/main','upstream/master','origin/main','origin/master']
                                  if shutil.which('git') and subprocess.run(['git','rev-parse','--verify',r],
                                     capture_output=True).returncode == 0), None)
                range_arg = f'{base_ref}..HEAD' if base_ref else 'HEAD'
                result = subprocess.run(['git', 'diff', '--name-only', range_arg], capture_output=True, text=True)
                changed_files = result.stdout.splitlines()
                if tf not in changed_files:
                    stale.append(f'{f} -> test not updated: {tf}')
                break
    for s in stale:
        print(s)
except Exception as e:
    pass
" 2>/dev/null || echo "")

      if [ -n "$STALE_TESTS" ]; then
        ISSUES+=("Stale tests detected — test files exist but were not updated for changed source:")
        while IFS= read -r s; do
          [ -n "$s" ] && ISSUES+=("  ✗ $s")
        done <<< "$STALE_TESTS"
      fi
      ;;
    BLOCKED)
      ISSUES+=("PRForge is BLOCKED — resolve blocker before pushing")
      ;;
    SHIPPED|COMPLETE)
      # Check if approval was already consumed — block re-shipping
      CONSUMED=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print('true' if d.get('approval', {}).get('consumed', False) else 'false')
except:
    print('false')
" 2>/dev/null || echo "false")
      if [ "$CONSUMED" = "true" ]; then
        APPROVAL_ID=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('approval', {}).get('approval_id', 'unknown'))
except:
    print('unknown')
" 2>/dev/null || echo "unknown")
        ISSUES+=("Approval already consumed (id: $APPROVAL_ID) — this PR was already shipped. Check $HARNESS_DIR/shipping_ledger.json for what was sent.")
      fi
      ;;
    ABORTED)
      WARNINGS+=("PRForge run was ABORTED — proceed with caution")
      ;;
    *)
      WARNINGS+=("Unknown PRForge phase: $PHASE")
      ;;
  esac
else
  ISSUES+=("No active PRForge state found — public actions require a PRForge run and approval artifact")
fi

# --- Force-push detection ---
if echo "$CMD" | grep -qiE 'push.*--force|push.*-f |force.push'; then
  JUSTIFICATION="$HARNESS_DIR/force_push_justification.md"
  if [ ! -f "$JUSTIFICATION" ]; then
    ISSUES+=("Force push requested but no justification artifact at $HARNESS_DIR/force_push_justification.md")
  fi
  # Warn about raw --force vs --force-with-lease
  if echo "$CMD" | grep -qE 'push.*--force[^-]|push.*-f[^-l]'; then
    if ! echo "$CMD" | grep -q 'force-with-lease'; then
      WARNINGS+=("Using --force instead of --force-with-lease — --force-with-lease is safer")
    fi
  fi
fi

# --- Validation ledger check ---
CONTRACT="$HARNESS_DIR/contract.md"
VALIDATION="$HARNESS_DIR/validation_ledger.md"
if [ -f "$CONTRACT" ] && [ ! -f "$VALIDATION" ]; then
  ISSUES+=("PR Contract exists but no validation ledger — run validation first")
fi

# --- Output ---
if [ ${#ISSUES[@]} -gt 0 ]; then
  prforge_write_redirect "$REPO_ROOT" "$HARNESS_DIR" "public_action_preflight" "$CMD" "upstream_public_action" "${PHASE:-UNKNOWN}" "regenerate package/approval after resolving preflight issues" "${PHASE:-APPROVAL}" "$ORIGINAL_OBJECTIVE" || true
  echo ""
  echo "=== PRForge Preflight Check ==="
  echo "Branch: ${CURRENT_BRANCH:-unknown}"
  echo "Phase: ${PHASE:-not active}"

  if [ ${#WARNINGS[@]} -gt 0 ]; then
    echo ""
    echo "Warnings:"
    for w in "${WARNINGS[@]}"; do
      echo "  ⚠ $w"
    done
    echo ""
  fi

  echo "DO NOT PUSH"
  echo ""
  echo "Reasons:"
  for i in "${ISSUES[@]}"; do
    echo "  ✗ $i"
  done
  echo ""
  prforge_redirect_message "$CMD" "Preflight found unsafe or incomplete release conditions." "local reads, approved edits, validation, packaging, approval regeneration" "resolve $HARNESS_DIR/redirects/current.json, then regenerate approval before retrying the public action" "${PHASE:-APPROVAL}"
  exit $FAIL
else
  if [ ${#WARNINGS[@]} -gt 0 ]; then
    echo "=== PRForge Preflight Warnings ===" >&2
    for w in "${WARNINGS[@]}"; do
      echo "  ⚠ $w" >&2
    done
  fi
  exit $PASS
fi
