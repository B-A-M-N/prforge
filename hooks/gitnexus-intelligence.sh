#!/bin/bash
# PRForge Intelligence Hook
# Fires automatically after Read/Grep/Glob operations.
# Auto-discovers available MCP servers and instructs the agent to use them.
#
# Intelligence source priority:
#   1. context-mode MCP — search_codebase, find_references, run_tests, typecheck, lint, git_*
#   2. GitNexus MCP     — repo intelligence, symbol search, blast radius, maintainer patterns
#   3. Local fallback   — rg, find, git log, gh CLI (always available)
#
# Note: GitHub (gh CLI) and Firecrawl are skill plugins, NOT MCP servers.
# GitHub API calls use `gh` CLI directly. Firecrawl is invoked via the firecrawl skill.

set +e  # PostToolUse hook: advisory only, never block on errors

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=hooks/prforge-common.sh
. "$SCRIPT_DIR/prforge-common.sh"

# Diagnostic: log hook invocation (minimal, for validation only)
mkdir -p "$(git rev-parse --show-toplevel 2>/dev/null)/.prforge" 2>/dev/null
echo "$(date -Iseconds) [gitnexus-intelligence] Read/Grep/Glob hook fired" >> "$(git rev-parse --show-toplevel 2>/dev/null)/.prforge/hook_events.log" 2>/dev/null || true

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
HARNESS_DIR=$(prforge_artifact_dir "$REPO_ROOT")
prforge_ensure_pointer "$REPO_ROOT" "$HARNESS_DIR" || exit 0

STATE_FILE="$HARNESS_DIR/state.json"
[ -f "$STATE_FILE" ] || exit 0

PHASE=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('phase', 'UNKNOWN'))
except:
    print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")

case "$PHASE" in
  INTAKE|INVESTIGATE|PLAN|SELF_REVIEW) ;;
  *) exit 0 ;;
esac

# ── Cache check: removed (was causing stale intelligence) ──────────
INTELLIGENCE_FILE="$HARNESS_DIR/repo_intelligence.md"
if [ -f "$INTELLIGENCE_FILE" ]; then
  # No longer skipping if updated within 90s, always re-evaluate.
  :
fi

# ── Auto-discover MCP servers ──────────────────────────────
# Check all known Claude Code MCP config locations
MCP_CONFIG=""
for p in \
  "$HOME/.claude/settings.json" \
  "$HOME/.claude/mcp.json" \
  "$HOME/.mcp.json" \
  "$REPO_ROOT/.mcp.json" \
  "$(ls -t "$HOME"/.claude.active*/mcp.json 2>/dev/null | head -1)"; do
  [ -f "$p" ] && MCP_CONFIG="$p" && break
done

GITNEXUS_MCP=false; CONTEXT_MODE_MCP=false

if [ -n "$MCP_CONFIG" ]; then
  eval "$(python3 -c "
import json
d = json.load(open('$MCP_CONFIG'))
servers = d.get('mcpServers', {})
print('GITNEXUS_MCP=' + str('gitnexus' in servers).lower())
cm = any('context' in k.lower() for k in servers)
print('CONTEXT_MODE_MCP=' + str(cm).lower())
" 2>/dev/null)"
fi

GITNEXUS_CLI=false
command -v gitnexus &>/dev/null && GITNEXUS_CLI=true

# ── Update state with intelligence mode ────────────────────
CURRENT_MODE=$(python3 -c "
import json
try:
    d = json.load(open('$STATE_FILE'))
    print(d.get('intelligence', {}).get('mode', ''))
except:
    print('')
" 2>/dev/null || echo "")

if [ -z "$CURRENT_MODE" ]; then
  GH_AVAILABLE=false
  gh auth status &>/dev/null 2>&1 && GH_AVAILABLE=true

  if [ "$GITNEXUS_MCP" = true ] || [ "$GITNEXUS_CLI" = true ]; then
    MODE="full_gitnexus"
    GITNEXUS_AVAIL=true
    RISK_FLOOR="low"
  elif [ "$GH_AVAILABLE" = true ]; then
    MODE="degraded_gh"
    GITNEXUS_AVAIL=false
    RISK_FLOOR="medium"
  else
    MODE="degraded_local"
    GITNEXUS_AVAIL=false
    RISK_FLOOR="medium"
  fi

  # Build disclosure
  MCP_NOTES=""
  [ "$CONTEXT_MODE_MCP" = true ] && MCP_NOTES+="context-mode MCP available. "

  if [ "$GITNEXUS_AVAIL" = true ]; then
    DISCLOSURE="GitNexus available. ${MCP_NOTES}Full intelligence mode."
  else
    DISCLOSURE="GitNexus unavailable. ${MCP_NOTES}Fallback: rg, git log, gh PR search. Risk impact: Medium."
  fi

  python3 -c "
import json
f = '$STATE_FILE'
d = json.load(open(f))
d.setdefault('intelligence', {})
d['intelligence']['mode'] = '$MODE'
d['intelligence']['gitnexus_available'] = $GITNEXUS_AVAIL
d['intelligence']['gh_available'] = $GH_AVAILABLE
d['intelligence']['mcp_gitnexus'] = $GITNEXUS_MCP
d['intelligence']['mcp_context_mode'] = $CONTEXT_MODE_MCP
d['intelligence']['minimum_risk_floor'] = '$RISK_FLOOR'
d['intelligence']['disclosure'] = '$DISCLOSURE'
if not $GITNEXUS_AVAIL:
    caps = ['prior_PR_analysis','semantic_search','maintainer_history','cross_repo_similarity','symbol_search','test_discovery','related_file_discovery']
    if '$CONTEXT_MODE_MCP' != 'true':
        caps.extend(['search_codebase','find_references','find_definition','run_tests','typecheck','lint'])
    d['intelligence']['unavailable_capabilities'] = caps
open(f, 'w').write(json.dumps(d, indent=2))
" 2>/dev/null || true
fi

# ── Write MCP tool instructions to repo_intelligence.md ────

# context-mode MCP instructions
if [ "$CONTEXT_MODE_MCP" = true ]; then
  python3 -c "
import os
f = '$INTELLIGENCE_FILE'
content = open(f).read() if os.path.exists(f) else '# Repo Intelligence\n'
section = '''
## ⚡ context-mode MCP Active
Prefer these MCP tools over raw bash commands:
- context-mode__search_codebase — search code patterns (replaces rg)
- context-mode__find_references — find all references to a symbol (replaces rg for blast radius)
- context-mode__find_definition — find symbol definition
- context-mode__run_tests — run test suite (replaces npm test / pytest / go test)
- context-mode__typecheck — run type checker (replaces npm run typecheck)
- context-mode__lint — run linter (replaces npm run lint)
- context-mode__git_diff / git_status / git_log — git operations
- context-mode__list_directory — list files (replaces find / ls)
'''
if 'context-mode MCP Active' not in content:
    content = section + '\n' + content
open(f, 'w').write(content)
" 2>/dev/null || true
fi

# GitNexus MCP instructions — tool names are gitnexus_* from AGENTS.md
if [ "$GITNEXUS_MCP" = true ]; then
  python3 -c "
import os
f = '$INTELLIGENCE_FILE'
content = open(f).read() if os.path.exists(f) else '# Repo Intelligence\n'
section = '''
## ⚡ GitNexus MCP Active
Use these tools for repo intelligence (actual tool names from GitNexus MCP server):
- mcp__gitnexus__query({query, repo?})          — hybrid search: find files, symbols, patterns
- mcp__gitnexus__context({name, repo?})         — 360° view of one symbol: callers, callees, tests
- mcp__gitnexus__impact({target, direction, repo?}) — blast radius before editing
- mcp__gitnexus__detect_changes({scope})        — map staged/unstaged diffs to affected symbols
- mcp__gitnexus__list_repos({})                 — discover indexed repos
- mcp__gitnexus__api_impact({route, method})    — pre-change API route impact
- mcp__gitnexus__cypher({query})                — custom graph queries for complex lookups
Use mcp__gitnexus__query() instead of find_related_files / find_symbol / find_ci_commands.
Use mcp__gitnexus__context() instead of find_tests_for_file.
Use mcp__gitnexus__impact() instead of find_recent_prs_touching.
'''
if 'GitNexus MCP Active' not in content:
    content = section + '\n' + content
open(f, 'w').write(content)
" 2>/dev/null || true
fi
# Note: GitHub and Firecrawl are skill plugins, not MCP servers.
# Use: gh CLI for GitHub API calls, firecrawl skill for web scraping.

# ── Local fallback intelligence ────────────────────────────
# Always runs. Provides basic context when MCP is unavailable.

if [ ! -f "$INTELLIGENCE_FILE" ]; then
  echo "# Repo Intelligence" > "$INTELLIGENCE_FILE"
  echo "" >> "$INTELLIGENCE_FILE"
fi

# Auto-discover test locations for changed files
CHANGED_FILES=$(git diff --name-only 2>/dev/null | head -20 || true)
if [ -n "$CHANGED_FILES" ]; then
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    dir=$(dirname "$f")
    base=$(basename "$f" | sed 's/\.[^.]*$//')
    NEARBY=$(find "$dir" -maxdepth 2 \( -name "${base}.test.*" -o -name "${base}.spec.*" \) 2>/dev/null | head -3 || true)
    if [ -n "$NEARBY" ]; then
      python3 -c "
import os
f = '$INTELLIGENCE_FILE'
content = open(f).read() if os.path.exists(f) else ''
entry = '## Fallback Test Discovery for $f\n$NEARBY\n'
if 'Fallback Test Discovery for $f' not in content:
    content += '\n' + entry
open(f, 'w').write(content)
" 2>/dev/null || true
    fi
  done <<< "$CHANGED_FILES"
fi

# Basic repo structure (only if file is small/empty)
INTELLIGENCE_SIZE=$(wc -c < "$INTELLIGENCE_FILE" 2>/dev/null || echo 0)
if [ "$INTELLIGENCE_SIZE" -lt 500 ]; then
  {
    echo ""
    echo "## Source Structure"
    find "$REPO_ROOT" -maxdepth 3 -type d \( -name "src" -o -name "lib" -o -name "packages" \) \
      -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/.prforge/*" 2>/dev/null | head -10
    echo ""
    echo "## Package Scripts"
    if [ -f "$REPO_ROOT/package.json" ]; then
      python3 -c "
import json
try:
    d = json.load(open('$REPO_ROOT/package.json'))
    for k, v in d.get('scripts', {}).items():
        print(f'  {k}: {v}')
except: pass
" 2>/dev/null || true
    fi
    echo ""
    echo "## Test Locations"
    find "$REPO_ROOT" -maxdepth 4 -type d \( -name "test" -o -name "tests" -o -name "__tests__" \) \
      -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/.prforge/*" 2>/dev/null | head -10
  } >> "$INTELLIGENCE_FILE" 2>/dev/null || true
fi

exit 0
