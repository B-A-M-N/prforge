#!/bin/bash
# PRForge Pre-Commit Hook
# Checks staged files before commit (Guard #4)
# NOTE: commit message hygiene is enforced by commit-msg.sh, not here.
# pre-commit receives NO arguments from git — message file is only available in commit-msg.

set -euo pipefail

VIOLATIONS=()
ARTIFACT_PATTERNS='(^|/)\.prforge(/|$)|(^|/)\.prforge-run$|(^|/)\.prforge-[^/]+'

# Guard #4: Check staged files for PRForge artifacts and pointers.
STAGED_ARTIFACTS=$(git diff --cached --name-only 2>/dev/null | grep -E "$ARTIFACT_PATTERNS" || true)
if [ -n "$STAGED_ARTIFACTS" ]; then
  VIOLATIONS+=("PRForge artifacts staged for commit:")
  while IFS= read -r f; do
    VIOLATIONS+=("  - $f")
  done <<< "$STAGED_ARTIFACTS"
fi

TRACKED_ARTIFACTS=$(git ls-files 2>/dev/null | grep -E "$ARTIFACT_PATTERNS" || true)
if [ -n "$TRACKED_ARTIFACTS" ]; then
  VIOLATIONS+=("PRForge artifacts are tracked in git:")
  while IFS= read -r f; do
    VIOLATIONS+=("  - $f")
  done <<< "$TRACKED_ARTIFACTS"
fi

STAGED_SYMLINKS=$(git diff --cached --name-only --diff-filter=AT 2>/dev/null | while IFS= read -r f; do
  [ -L "$f" ] && printf "%s\n" "$f"
done || true)
if [ -n "$STAGED_SYMLINKS" ]; then
  while IFS= read -r f; do
    target=$(readlink "$f" 2>/dev/null || true)
    case "$target" in
      "$HOME/.prforge"*|*/.prforge/*|*.prforge*)
        VIOLATIONS+=("Symlink to PRForge artifact staged: $f -> $target")
        ;;
    esac
  done <<< "$STAGED_SYMLINKS"
fi

# Guard: Check staged files for debug artifacts
STAGED_DEBUG=$(git diff --cached --name-only 2>/dev/null | grep -iE '\.(log|tmp|bak)$|debug\.' || true)
if [ -n "$STAGED_DEBUG" ]; then
  VIOLATIONS+=("Debug/temp files staged: $STAGED_DEBUG")
fi

if [ ${#VIOLATIONS[@]} -gt 0 ]; then
  echo ""
  echo "=== PRForge Pre-Commit Check ==="
  echo "BLOCKED — staged file violations:"
  for v in "${VIOLATIONS[@]}"; do
    echo "  ✗ $v"
  done
  echo ""
  echo "To override: git commit --no-verify"
  exit 1
fi

exit 0
