#!/usr/bin/env bash
# certify.sh — PRForge Level 0–1 certification runner
# Usage: bash scripts/certify.sh [--save-output]
#
# Exit codes:
#   0 — Level 0 fully certified
#   1 — One or more Level 0 checks failed
#   2 — Runner itself encountered an error

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SAVE_OUTPUT=0
if [[ "${1:-}" == "--save-output" ]]; then
  SAVE_OUTPUT=1
fi

OUTFILE="$ROOT/docs/level0-certification-run.txt"
TMPOUT="$(mktemp)"
trap 'rm -f "$TMPOUT"' EXIT

log() { echo "$*" | tee -a "$TMPOUT"; }
sep() { log ""; log "$(printf '=%.0s' {1..70})"; log "$1"; log "$(printf '=%.0s' {1..70})"; }

PASS_COUNT=0
FAIL_COUNT=0

pass() { log "  PASS  $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { log "  FAIL  $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
skip() { log "  SKIP  $1"; }

sep "PRForge Level 0 Certification — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log "Root: $ROOT"
log ""

# ── Shell syntax ─────────────────────────────────────────────────────────────
sep "Shell syntax"
SYNTAX_FAIL=0
for f in "$ROOT"/hooks/*.sh "$ROOT"/monitors/*.sh "$ROOT"/scripts/*.sh; do
  [[ -f "$f" ]] || continue
  if bash -n "$f" 2>/dev/null; then
    pass "syntax: $(basename "$f")"
  else
    fail "syntax: $(basename "$f")"
    SYNTAX_FAIL=1
  fi
done

# ── Phase machine validation ──────────────────────────────────────────────────
sep "Phase machine"
PM_OUT="$(python3 "$ROOT/scripts/validate_phase_machine.py" 2>&1)"
if echo "$PM_OUT" | grep -qE "PASS|VALID"; then
  pass "phase machine is consistent"
else
  fail "phase machine: $PM_OUT"
fi

# ── Artifact pollution ────────────────────────────────────────────────────────
sep "Artifact pollution"
DIRTY="$(git -C "$ROOT" status --short 2>/dev/null | grep -v '^??' || true)"
if [[ -z "$DIRTY" ]]; then
  pass "worktree clean (no uncommitted tracked changes)"
else
  fail "uncommitted changes detected:"
  log "$DIRTY"
fi

PRFORGE_DIRS="$(find "$ROOT" -maxdepth 3 -name '.prforge' -type d 2>/dev/null | grep -v '/tmp/' || true)"
if [[ -z "$PRFORGE_DIRS" ]]; then
  pass "no .prforge/ dirs inside repo tree"
else
  fail ".prforge/ found inside repo (artifact pollution):"
  log "$PRFORGE_DIRS"
fi

# ── Python unit tests ─────────────────────────────────────────────────────────
sep "Python unit tests"

run_python_test() {
  local label="$1"
  local script="$2"
  local out
  if out="$(python3 "$script" 2>&1)"; then
    pass "$label"
  else
    fail "$label"
    log "$out"
  fi
}

run_python_test "quality_weakness_gate tests (10)" \
  "$ROOT/scripts/tests/quality_gate/test_quality_weakness_gate.py"

run_python_test "git_state_check tests (8)" \
  "$ROOT/scripts/tests/git_state/test_git_state_check.py"

# Memory tests
if [[ -f "$ROOT/scripts/tests/memory/test_memory_ledger.py" ]]; then
  run_python_test "memory ledger tests" \
    "$ROOT/scripts/tests/memory/test_memory_ledger.py"
else
  skip "memory ledger tests (not found)"
fi

# Discovery tests
if [[ -f "$ROOT/scripts/tests/discovery/test_candidate_scoring.py" ]]; then
  run_python_test "candidate discovery tests" \
    "$ROOT/scripts/tests/discovery/test_candidate_scoring.py"
else
  skip "candidate discovery tests (not found)"
fi

# ── Hook regression tests ─────────────────────────────────────────────────────
sep "Hook regression tests"
HOOK_OUT="$TMPOUT.hooks"
if bash "$ROOT/scripts/tests/hooks/test_prforge_regressions.sh" >"$HOOK_OUT" 2>&1; then
  HOOK_COUNT="$(grep -c '^PASS:' "$HOOK_OUT" || echo 0)"
  pass "test_prforge_regressions.sh — $HOOK_COUNT tests passed"
else
  HOOK_FAIL="$(grep '^FAIL:' "$HOOK_OUT" | head -5)"
  fail "test_prforge_regressions.sh failures:"
  log "$HOOK_FAIL"
fi
rm -f "$HOOK_OUT"

# Preflight tests
if [[ -f "$ROOT/scripts/tests/hooks/test_preflight.sh" ]]; then
  PREFLIGHT_OUT="$TMPOUT.preflight"
  if bash "$ROOT/scripts/tests/hooks/test_preflight.sh" >"$PREFLIGHT_OUT" 2>&1; then
    PRE_COUNT="$(grep -c '^PASS:' "$PREFLIGHT_OUT" || echo 0)"
    pass "test_preflight.sh — $PRE_COUNT tests passed"
  else
    PRE_FAIL="$(grep '^FAIL:' "$PREFLIGHT_OUT" | head -5)"
    fail "test_preflight.sh failures:"
    log "$PRE_FAIL"
  fi
  rm -f "$PREFLIGHT_OUT"
else
  skip "test_preflight.sh (not found)"
fi

# ── Gate wiring verification ──────────────────────────────────────────────────
sep "Gate wiring checks"

# Quality weakness gate reachable from hook
if grep -q "quality_weakness_gate.py" "$ROOT/hooks/phase-boundary.sh"; then
  pass "quality_weakness_gate.py referenced in phase-boundary.sh"
else
  fail "quality_weakness_gate.py NOT found in phase-boundary.sh"
fi

# Git state gate reachable from hook
if grep -q "git_state_check.py\|git_state.json" "$ROOT/hooks/phase-boundary.sh"; then
  pass "git_state gate referenced in phase-boundary.sh"
else
  fail "git_state gate NOT found in phase-boundary.sh"
fi

# pr_approve.py checks quality_weakness
if grep -q "quality_weakness" "$ROOT/scripts/pr_approve.py"; then
  pass "pr_approve.py checks quality_weakness"
else
  fail "pr_approve.py does NOT check quality_weakness"
fi

# pr_approve.py checks git_state
if grep -q "git_state" "$ROOT/scripts/pr_approve.py"; then
  pass "pr_approve.py checks git_state"
else
  fail "pr_approve.py does NOT check git_state"
fi

# ── No legacy SHIPPED references ──────────────────────────────────────────────
# Excluded: skills/prforge/phases/shipped.md (the legacy migration playbook itself)
#           hooks/preflight.sh (handles SHIPPED as a backward-compat terminal state)
sep "Legacy reference audit"
SHIPPED_REFS="$(grep -r "SHIPPED" "$ROOT/hooks/" "$ROOT/commands/" "$ROOT/skills/" \
  --include='*.md' --include='*.sh' -l 2>/dev/null \
  | grep -v '/legacy/' \
  | grep -v 'phases/shipped.md' \
  | grep -v 'hooks/preflight.sh' \
  || true)"
if [[ -z "$SHIPPED_REFS" ]]; then
  pass "no unexpected SHIPPED state references in hooks/commands/skills"
else
  fail "unexpected SHIPPED state found in: $SHIPPED_REFS"
fi

# ── Level 1 status ────────────────────────────────────────────────────────────
sep "Level 1 — simulation status"
if [[ -f "$ROOT/scripts/tests/level1/simulate_full_run.sh" ]]; then
  log "  INFO  simulate_full_run.sh exists — attempting Level 1 run..."
  if bash "$ROOT/scripts/tests/level1/simulate_full_run.sh" >>"$TMPOUT" 2>&1; then
    pass "Level 1 simulation — PASS"
  else
    fail "Level 1 simulation — FAIL"
  fi
else
  skip "Level 1: simulate_full_run.sh not yet created (Issue #3 — not in current queue)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
sep "SUMMARY"
TOTAL=$((PASS_COUNT + FAIL_COUNT))
log "  Passed: $PASS_COUNT / $TOTAL"
log "  Failed: $FAIL_COUNT / $TOTAL"
log ""

if [[ $FAIL_COUNT -eq 0 ]]; then
  log "  LEVEL 0: CERTIFIED"
  RC=0
else
  log "  LEVEL 0: PARTIAL ($FAIL_COUNT check(s) failed)"
  RC=1
fi
log ""
log "Run: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log "Branch: $(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
log "HEAD: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"

if [[ $SAVE_OUTPUT -eq 1 ]]; then
  cp "$TMPOUT" "$OUTFILE"
  log ""
  log "Output saved to: $OUTFILE"
fi

cat "$TMPOUT"
exit $RC
