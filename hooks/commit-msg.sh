#!/bin/bash
# PRForge Commit-Msg Hook
# Checks commit message content (Guard #6)
# Git passes the commit message file path as $1 to commit-msg hooks.

set -euo pipefail

COMMIT_MSG_FILE="${1:-}"
VIOLATIONS=()

if [ -z "$COMMIT_MSG_FILE" ] || [ ! -f "$COMMIT_MSG_FILE" ]; then
  exit 0
fi

MSG=$(cat "$COMMIT_MSG_FILE")

# Block WIP/debug/temp commit names
if echo "$MSG" | grep -qiE '^(WIP|debug|temp|fixup|squash)[\s:!]|^WIP$|^debug$|^temp$'; then
  VIOLATIONS+=("Commit message starts with forbidden pattern (WIP/debug/temp/fixup/squash)")
fi

# Block AI co-author trailers
if echo "$MSG" | grep -qiE 'Co-authored-by[[:space:]]*:[[:space:]]*(Claude|Opus|Sonnet|Haiku|Anthropic|ChatGPT|GPT|Gemini|Copilot)'; then
  VIOLATIONS+=("Commit contains AI co-author trailer — commits are authored by the human git identity only")
fi

# Block any Co-authored-by trailer (PRForge commits are sole-authored)
if echo "$MSG" | grep -qi 'Co-authored-by'; then
  VIOLATIONS+=("Commit contains Co-authored-by trailer — not allowed in upstream PRs")
fi

# Block AI-generated footers and tool attribution
if echo "$MSG" | grep -qiE 'Generated (by|with) (Claude|AI|ChatGPT)|AI-generated|AI-assisted|Claude Code|Anthropic'; then
  VIOLATIONS+=("Commit contains AI attribution footer — remove it")
fi

# Block commits with obvious debug content in message
if echo "$MSG" | grep -qiE '^(TODO: remove|FIXME|console\.log|print\(\"debug)'; then
  VIOLATIONS+=("Commit message references debug artifacts — clean up first")
fi

if [ ${#VIOLATIONS[@]} -gt 0 ]; then
  echo ""
  echo "=== PRForge Commit-Msg Check ==="
  echo "BLOCKED — commit message violations:"
  for v in "${VIOLATIONS[@]}"; do
    echo "  ✗ $v"
  done
  echo ""
  echo "To override: git commit --no-verify"
  exit 1
fi

exit 0
