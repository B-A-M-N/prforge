#!/usr/bin/env bash
# PRForge Level 0 certification runner.
# Runs all static/local integrity checks and writes output to
# .prforge-certification/level0/latest.txt (gitignored).
# Exits 0 if all pass, 1 if any fail.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$ROOT/.prforge-certification/level0"
OUTPUT_FILE="$OUTPUT_DIR/latest.txt"

mkdir -p "$OUTPUT_DIR"
: > "$OUTPUT_FILE"

PASS_COUNT=0
FAIL_COUNT=0
FAILURES=()

_pass() { PASS_COUNT=$((PASS_COUNT + 1)); printf "PASS: %s\n" "$1" | tee -a "$OUTPUT_FILE"; }
_fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); FAILURES+=("$1"); printf "FAIL: %s\n" "$1" | tee -a "$OUTPUT_FILE"; }

printf "PRForge Level 0 Certification — %s\n\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee "$OUTPUT_FILE"

# ── 1. Shell syntax ───────────────────────────────────────────────────────────
printf "\n[1/10] Shell syntax\n" | tee -a "$OUTPUT_FILE"
SYNTAX_OK=true
for _sh in "$ROOT"/hooks/*.sh "$ROOT"/monitors/*.sh; do
  if ! bash -n "$_sh" >> "$OUTPUT_FILE" 2>&1; then
    printf "  syntax error: %s\n" "$_sh" | tee -a "$OUTPUT_FILE"
    SYNTAX_OK=false
  fi
done
if $SYNTAX_OK; then
  _pass "shell syntax: hooks/*.sh monitors/*.sh"
else
  _fail "shell syntax: hooks/*.sh monitors/*.sh"
fi

# ── 2. Python compile ─────────────────────────────────────────────────────────
printf "\n[2/10] Python compile\n" | tee -a "$OUTPUT_FILE"
COMPILE_OK=true
while IFS= read -r -d '' f; do
  if ! python3 -m py_compile "$f" >> "$OUTPUT_FILE" 2>&1; then
    COMPILE_OK=false
    printf "  compile error: %s\n" "$f" | tee -a "$OUTPUT_FILE"
  fi
done < <(find "$ROOT/scripts" -name '*.py' ! -path '*/__pycache__/*' -print0 | sort -z)
if $COMPILE_OK; then
  _pass "python compile: all scripts"
else
  _fail "python compile: one or more scripts failed"
fi

# ── 3. Phase machine ──────────────────────────────────────────────────────────
printf "\n[3/10] Phase machine\n" | tee -a "$OUTPUT_FILE"
if python3 "$ROOT/scripts/validate_phase_machine.py" >> "$OUTPUT_FILE" 2>&1; then
  _pass "phase machine: valid"
else
  _fail "phase machine: invalid"
fi

# ── 4. Hook regression ────────────────────────────────────────────────────────
printf "\n[4/10] Hook regression\n" | tee -a "$OUTPUT_FILE"
if bash "$ROOT/scripts/tests/hooks/test_prforge_regressions.sh" >> "$OUTPUT_FILE" 2>&1; then
  _pass "hook regression: all pass"
else
  _fail "hook regression: failures detected"
fi

# ── 5. Preflight test ─────────────────────────────────────────────────────────
printf "\n[5/10] Preflight test\n" | tee -a "$OUTPUT_FILE"
if bash "$ROOT/scripts/tests/hooks/test_preflight.sh" >> "$OUTPUT_FILE" 2>&1; then
  _pass "preflight test: all pass"
else
  _fail "preflight test: failures detected"
fi

# ── 6. Memory indexing regression ─────────────────────────────────────────────
printf "\n[6/10] Memory indexing regression\n" | tee -a "$OUTPUT_FILE"
if python3 "$ROOT/scripts/tests/memory/test_memory_indexing_regression.py" >> "$OUTPUT_FILE" 2>&1; then
  _pass "memory indexing regression: pass"
else
  _fail "memory indexing regression: failed"
fi

# ── 7. Candidate scoring regression ──────────────────────────────────────────
printf "\n[7/10] Candidate scoring regression\n" | tee -a "$OUTPUT_FILE"
if python3 "$ROOT/scripts/tests/discovery/test_candidate_scoring_regression.py" >> "$OUTPUT_FILE" 2>&1; then
  _pass "candidate scoring regression: pass"
else
  _fail "candidate scoring regression: failed"
fi

# ── 8. Mesh Redis integration ─────────────────────────────────────────────────
printf "\n[8/10] Mesh Redis integration\n" | tee -a "$OUTPUT_FILE"
if python3 "$ROOT/scripts/tests/mesh/test_mesh_redis_integration.py" >> "$OUTPUT_FILE" 2>&1; then
  _pass "mesh Redis integration: pass"
else
  _fail "mesh Redis integration: failed"
fi

# ── 9. Artifact pollution ─────────────────────────────────────────────────────
printf "\n[9/10] Artifact pollution\n" | tee -a "$OUTPUT_FILE"
DIRTY=$(git -C "$ROOT" status --short 2>/dev/null \
  | grep -v '\.prforge-certification/' \
  | grep -v '\.prforge-run' || true)
if [ -z "$DIRTY" ]; then
  _pass "artifact pollution: working tree clean"
else
  printf "%s\n" "$DIRTY" >> "$OUTPUT_FILE"
  _fail "artifact pollution: unexpected files in working tree"
fi

# ── 10. No repo-local .prforge dirs ──────────────────────────────────────────
printf "\n[10/10] No repo-local .prforge dirs\n" | tee -a "$OUTPUT_FILE"
PRFORGE_DIRS=$(find "$ROOT" -maxdepth 3 -type d -name '.prforge' 2>/dev/null || true)
if [ -z "$PRFORGE_DIRS" ]; then
  _pass "repo-local .prforge dirs: none found"
else
  printf "%s\n" "$PRFORGE_DIRS" >> "$OUTPUT_FILE"
  _fail "repo-local .prforge dirs: found (must not exist)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
printf "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n" | tee -a "$OUTPUT_FILE"
printf "Level 0: %d/10 passed\n" "$PASS_COUNT" | tee -a "$OUTPUT_FILE"

if [ "$FAIL_COUNT" -gt 0 ]; then
  printf "FAILED (%d):\n" "$FAIL_COUNT" | tee -a "$OUTPUT_FILE"
  for f in "${FAILURES[@]}"; do printf "  ✗ %s\n" "$f" | tee -a "$OUTPUT_FILE"; done
  printf "\nFull output: %s\n" "$OUTPUT_FILE"
  exit 1
else
  printf "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n" | tee -a "$OUTPUT_FILE"
  printf "Full output: %s\n" "$OUTPUT_FILE"
  exit 0
fi
