#!/usr/bin/env bash
# Preflight hook regression tests
# Covers: upstream push blocking, option-form upstream push blocking,
#         raw --force blocking, force-with-lease behavior,
#         no repo-local artifact creation in inactive/hot-path mode,
#         missing CLAUDE_PLUGIN_ROOT fallback behavior.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

pass() {
  echo "PASS: $1"
}

TMP="$(mktemp -d /tmp/prforge-preflight.XXXXXX)"
REPO="$TMP/repo"
mkdir -p "$REPO"
cd "$REPO"
git init -q
git config user.email test@example.com
git config user.name Test
printf 'one\n' > a.txt
git add a.txt
git commit -q -m init

# ── Helpers ──────────────────────────────────────────────────────────

# Run preflight hook with a given command, capture exit code and output
run_preflight() {
  local cmd="$1"
  local json
  json=$(python3 -c "import json; print(json.dumps({'tool_name':'Bash','tool_input':{'command':'$cmd'}}))")
  printf '%s' "$json" | bash "$ROOT/hooks/preflight.sh" >"$TMP/preflight.out" 2>"$TMP/preflight.err" || true
}

# Set up a minimal APPROVAL-phase state so preflight proceeds past early exits
setup_approval_state() {
  mkdir -p .prforge
  cat > .prforge/state.json <<JSON
{
  "version": "1.0",
  "phase": "APPROVAL",
  "task": {"objective": "test"},
  "release": {"approval_status": "READY_TO_SHIP"},
  "approval": {
    "approval_id": "test-1",
    "approved": true,
    "approved_actions": ["push"]
  }
}
JSON
}

# ── 1. Upstream push blocking ────────────────────────────────────────

setup_approval_state
run_preflight "git push upstream feature-branch"
if ! grep -q "DO NOT PUSH" "$TMP/preflight.err" 2>/dev/null; then
  # Also check stdout
  if ! grep -q "DO NOT PUSH" "$TMP/preflight.out" 2>/dev/null; then
    echo "--- stdout ---" >&2
    cat "$TMP/preflight.out" >&2 || true
    echo "--- stderr ---" >&2
    cat "$TMP/preflight.err" >&2 || true
    fail "preflight did not block 'git push upstream feature-branch'"
  fi
fi
pass "1. blocks upstream push"

# ── 2. Option-form upstream push blocking: git push -u upstream branch ──

setup_approval_state
run_preflight "git push -u upstream feature-branch"
if ! grep -q "DO NOT PUSH" "$TMP/preflight.err" 2>/dev/null; then
  if ! grep -q "DO NOT PUSH" "$TMP/preflight.out" 2>/dev/null; then
    echo "--- stdout ---" >&2
    cat "$TMP/preflight.out" >&2 || true
    echo "--- stderr ---" >&2
    cat "$TMP/preflight.err" >&2 || true
    fail "preflight did not block 'git push -u upstream feature-branch'"
  fi
fi
pass "2. blocks option-form upstream push (-u upstream)"

# ── 3. Raw --force blocking ──────────────────────────────────────────

setup_approval_state
run_preflight "git push --force origin feature-branch"
if ! grep -q "DO NOT PUSH\|force" "$TMP/preflight.err" 2>/dev/null; then
  if ! grep -q "DO NOT PUSH\|force" "$TMP/preflight.out" 2>/dev/null; then
    echo "--- stdout ---" >&2
    cat "$TMP/preflight.out" >&2 || true
    echo "--- stderr ---" >&2
    cat "$TMP/preflight.err" >&2 || true
    fail "preflight did not block 'git push --force'"
  fi
fi
pass "3. blocks raw --force push"

# ── 4. Force-with-lease behavior ─────────────────────────────────────

setup_approval_state
# force-with-lease without --force flag should not trigger the raw-force guard
# but should still warn about using --force instead of --force-with-lease
run_preflight "git push --force-with-lease origin feature-branch"
# This should either pass (no issues) or warn about force — either is acceptable
# The key is it should NOT block with "raw git push --force" message
if grep -q "raw git push --force" "$TMP/preflight.err" 2>/dev/null; then
  echo "--- stderr ---" >&2
  cat "$TMP/preflight.err" >&2 || true
  fail "preflight incorrectly treated --force-with-lease as raw --force"
fi
pass "4. allows force-with-lease (does not treat as raw --force)"

# ── 5. No repo-local artifact creation in inactive/hot-path mode ─────

# Clean state: no .prforge-run, no .prforge/state.json
rm -rf .prforge .prforge-run
# Run preflight with a non-git command (should exit early, no artifacts)
run_preflight "ls -la"
if [ -e ".prforge-run" ] || [ -e ".prforge" ]; then
  fail "preflight created repo-local artifacts in inactive mode"
fi
pass "5. no repo-local artifacts in inactive mode"

# ── 6. Missing CLAUDE_PLUGIN_ROOT fallback behavior ──────────────────

# When CLAUDE_PLUGIN_ROOT is unset and mesh config exists in default location,
# the mesh-lock-guard hook should still resolve correctly (not hang, not crash)
MESH_OFF_CONFIG="$TMP/mesh-off.json"
cat > "$MESH_OFF_CONFIG" <<'JSON'
{"mode": "off", "worker_id": "w1"}
JSON
GUARD_JSON=$(python3 -c "import json; print(json.dumps({'tool_name':'Edit','tool_input':{'file_path':'$REPO/a.txt'}}))")
if ! printf '%s' "$GUARD_JSON" | \
  HOME="$TMP/no-home" \
  PRFORGE_MESH_CONFIG="$MESH_OFF_CONFIG" \
  PRFORGE_WORKER_ID="w1" \
  CLAUDE_PLUGIN_ROOT="" \
  bash "$ROOT/hooks/mesh-lock-guard.sh" >"$TMP/guard.out" 2>"$TMP/guard.err"; then
  # mesh-off mode should allow, not block
  echo "--- guard stderr ---" >&2
  cat "$TMP/guard.err" >&2 || true
  fail "mesh-lock-guard blocked in off mode without CLAUDE_PLUGIN_ROOT"
fi
pass "6. mesh-lock-guard works without CLAUDE_PLUGIN_ROOT (off mode)"

# ── 7. Preflight allows safe read-only commands ──────────────────────

rm -rf .prforge .prforge-run
run_preflight "git status"
# Should exit 0 without creating artifacts
if [ -e ".prforge-run" ] || [ -e ".prforge" ]; then
  fail "preflight created artifacts for read-only git status"
fi
pass "7. allows read-only git commands without artifacts"

# ── 8. Preflight blocks push in early phases ─────────────────────────

mkdir -p .prforge
cat > .prforge/state.json <<JSON
{
  "version": "1.0",
  "phase": "IMPLEMENT",
  "task": {"objective": "test"}
}
JSON
run_preflight "git push origin feature-branch"
if ! grep -q "DO NOT PUSH\|IMPLEMENT" "$TMP/preflight.err" 2>/dev/null; then
  if ! grep -q "DO NOT PUSH\|IMPLEMENT" "$TMP/preflight.out" 2>/dev/null; then
    echo "--- stdout ---" >&2
    cat "$TMP/preflight.out" >&2 || true
    echo "--- stderr ---" >&2
    cat "$TMP/preflight.err" >&2 || true
    fail "preflight did not block push during IMPLEMENT phase"
  fi
fi
pass "8. blocks push during IMPLEMENT phase"

# ── Summary ──────────────────────────────────────────────────────────

rm -rf "$TMP"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "All preflight regression tests passed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
