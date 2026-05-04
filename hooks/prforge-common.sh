#!/bin/bash
# Shared helpers for PRForge hooks.
#
# Invariant: repo-local state is a plain text pointer file only. Never symlink
# repo/.prforge to ~/.prforge/runs/...; hooks and tooling should resolve the
# pointer explicitly.

prforge_repo_slug() {
  local root="$1"
  local remote slug
  remote=$(git -C "$root" config --get remote.origin.url 2>/dev/null || basename "$root")
  slug=$(printf "%s" "$remote" |
    sed -E 's#^git@github.com:##; s#^https://github.com/##; s#\.git$##; s#[^A-Za-z0-9._/-]+#_#g; s#/#__#g')
  printf "%s" "${slug:-unknown_repo}"
}

prforge_run_key() {
  local root="$1"
  local branch pr
  pr=$(git -C "$root" config --get branch.$(git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null).prforge-pr 2>/dev/null || true)
  if [ -n "$pr" ]; then
    printf "pr-%s" "$pr"
    return
  fi
  branch=$(git -C "$root" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "detached")
  printf "%s" "$branch" | sed -E 's#[^A-Za-z0-9._-]+#_#g'
}

prforge_artifact_dir() {
  local root="$1"
  local pointer="$root/.prforge-run"
  local artifact_dir run_id slug key

  if [ -f "$pointer" ]; then
    artifact_dir=$(awk -F= '$1=="artifact_dir"{print $2}' "$pointer" 2>/dev/null | tail -1)
    if [ -n "$artifact_dir" ]; then
      printf "%s" "$artifact_dir"
      return
    fi
  fi

  if [ -f "$root/.prforge/state.json" ]; then
    printf "%s" "$root/.prforge"
    return
  fi

  slug=$(prforge_repo_slug "$root")
  key=$(prforge_run_key "$root")
  run_id=$(date -u +%Y%m%d-%H%M%S)
  printf "%s/runs/%s/%s/%s" "${PRFORGE_HOME:-$HOME/.prforge}" "$slug" "$key" "$run_id"
}

prforge_ensure_pointer() {
  local root="$1"
  local artifact_dir="$2"
  local pointer="$root/.prforge-run"
  mkdir -p "$artifact_dir" "$artifact_dir/redirects" || return 1
  if [ -L "$root/.prforge" ] || [ -L "$pointer" ]; then
    echo "PRForge refused to use symlinked repo-local state." >&2
    return 1
  fi
  {
    printf "run_id=%s\n" "$(basename "$artifact_dir")"
    printf "artifact_dir=%s\n" "$artifact_dir"
  } > "$pointer"

  if git -C "$root" rev-parse --git-dir >/dev/null 2>&1; then
    local exclude
    exclude="$(git -C "$root" rev-parse --git-dir)/info/exclude"
    mkdir -p "$(dirname "$exclude")"
    for pat in ".prforge/" ".prforge-run" ".prforge-*"; do
      grep -qxF "$pat" "$exclude" 2>/dev/null || printf "%s\n" "$pat" >> "$exclude"
    done
  fi
}

prforge_write_redirect() {
  local root="$1"
  local artifact_dir="$2"
  local reason="$3"
  local blocked_action="$4"
  local target="$5"
  local current_phase="$6"
  local required_next_action="$7"
  local return_phase="$8"
  local original_objective="$9"

  mkdir -p "$artifact_dir/redirects"
  python3 - "$artifact_dir/redirects/current.json" \
    "$reason" "$blocked_action" "$target" "$current_phase" \
    "$required_next_action" "$return_phase" "$original_objective" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
reason, blocked_action, target, phase, next_action, return_phase, objective = sys.argv[2:9]
count = 1
if path.exists():
    try:
        old = json.loads(path.read_text())
        if old.get("reason") == reason and old.get("target") == target:
            count = int(old.get("redirect_count", 0)) + 1
    except Exception:
        pass

budget = 10
budget_file = path.parent.parent.parent / "mesh_config.json"
if budget_file.exists():
    try:
        d = json.loads(budget_file.read_text())
        rb = d.get("redirect_budget", {})
        for key in ["unexpected_file", "missing_state_sync", "phase_exit_without_report",
                    "validation_failure", "contract_mismatch", "public_action_without_approval"]:
            if key in reason or key.replace("_", "") in reason:
                budget = rb.get(key, 10)
                break
        else:
            budget = rb.get(reason, 10)
    except Exception:
        pass

if budget > 0 and count >= budget:
    print(f"REDIRECT BUDGET EXCEEDED for {reason} (count={count}, budget={budget})", file=sys.stderr)
    print("Escalating to BLOCKED state.", file=sys.stderr)
    state_file = path.parent.parent / "state.json"
    if state_file.exists():
        try:
            sd = json.loads(state_file.read_text())
            sd["phase"] = "BLOCKED"
            sd["blocker"] = f"Redirect budget exceeded for {reason} (count={count})"
            state_file.write_text(json.dumps(sd, indent=2))
        except Exception:
            pass
    sys.exit(1)

packet = {
    "type": "redirect",
    "severity": "recoverable",
    "reason": reason,
    "blocked_action": blocked_action,
    "target": target,
    "current_phase": phase or "UNKNOWN",
    "original_objective": objective or "unknown",
    "allowed_actions": [
        "read",
        "edit_approved_files",
        "run_tests",
        "request_scope_expansion",
        "regenerate_package_or_approval",
    ],
    "blocked_actions": [blocked_action],
    "required_next_action": next_action,
    "return_to_phase_after_recovery": return_phase or phase or "UNKNOWN",
    "redirect_count": count,
    "do_not_treat_redirect_as_completion": True,
    "created_at": datetime.now(timezone.utc).isoformat(),
}
path.write_text(json.dumps(packet, indent=2) + "\n")
PY
}

prforge_redirect_message() {
  local blocked="$1"
  local reason="$2"
  local allowed="$3"
  local next="$4"
  local return_phase="$5"
  echo "PRForge redirected this action."
  echo ""
  echo "Blocked:"
  echo "  $blocked"
  echo ""
  echo "Reason:"
  echo "  $reason"
  echo ""
  echo "Still allowed:"
  echo "  $allowed"
  echo ""
  echo "Required next step:"
  echo "  $next"
  echo ""
  echo "After recovery:"
  echo "  return to $return_phase and continue the original objective."
}

# ── File locking helpers for state.json ──
# Usage: prforge_lock_state /path/to/state.json
#        ... commands that read/write state.json ...
#        prforge_unlock_state /path/to/state.json
#
# Uses flock on fd 200; timeout 10s for exclusive lock.
# Lock file: state.json.lock (created alongside state.json).
# A dedicated fd (200) avoids conflicts with subprocesses using fd 9.

prforge_lock_state() {
  local state_file="$1"
  local lock_file="${state_file}.lock"
  mkdir -p "$(dirname "$lock_file")" 2>/dev/null
  exec 200>"$lock_file"
  if ! flock -w 10 200 2>/dev/null; then
    echo "WARNING: Could not acquire lock for $state_file after 10s" >&2
    return 1
  fi
}

prforge_unlock_state() {
  # Closing fd 200 releases the flock
  exec 200>&- 2>/dev/null
  local state_file="$1"
  local lock_file="${state_file}.lock"
  rm -f "$lock_file" 2>/dev/null
}

# Convenience: run a function under lock, auto-unlocking on exit.
# Usage: prforge_with_lock /path/to/state.json function_name [args...]
prforge_with_lock() {
  local state_file="$1"
  shift
  prforge_lock_state "$state_file" || return 1
  # shellcheck disable=SC2068
  $@ ; local rc=$?
  prforge_unlock_state "$state_file"
  return $rc
}

# Path to the Python state helper
prforge_state_py() {
  local script_dir="$SCRIPT_DIR"
  # Walk up to find prforge root, then scripts/prforge_state.py
  local dir="$script_dir"
  for _ in 1 2 3 4 5; do
    if [ -f "$dir/scripts/prforge_state.py" ]; then
      echo "$dir/scripts/prforge_state.py"
      return 0
    fi
    [ "$dir" = "/" ] && break
    dir=$(dirname "$dir")
  done
  # Fallback: check common locations
  for p in \
    "$HOME/.prforge/scripts/prforge_state.py" \
    "/usr/local/lib/prforge/scripts/prforge_state.py"; do
    if [ -f "$p" ]; then
      echo "$p"
      return 0
    fi
  done
  echo ""
}
