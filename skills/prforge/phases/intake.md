# Phase 0 + 1: INTAKE (Safety Snapshot + Task Normalization)

Read this file at the START of INTAKE before doing any work.

---

## Phase 0: Safety Snapshot

**Auto-install git hooks if not present:**
```bash
# pre-commit: checks staged files (.prforge/ artifacts, debug files)
HOOK="$REPO_ROOT/.git/hooks/pre-commit"
HOOK_SRC="${CLAUDE_PLUGIN_ROOT}/hooks/pre-commit.sh"
if [ ! -f "$HOOK" ] && [ -f "$HOOK_SRC" ]; then
  cp "$HOOK_SRC" "$HOOK" && chmod +x "$HOOK"
fi

# commit-msg: checks commit message content (AI bylines, WIP, co-author trailers)
HOOK="$REPO_ROOT/.git/hooks/commit-msg"
HOOK_SRC="${CLAUDE_PLUGIN_ROOT}/hooks/commit-msg.sh"
if [ ! -f "$HOOK" ] && [ -f "$HOOK_SRC" ]; then
  cp "$HOOK_SRC" "$HOOK" && chmod +x "$HOOK"
fi
```
Silent and safe — only installs if no hook already exists at that path.

**Resolve `$ARTIFACT_DIR`, then verify git identity and write to `state.json`:**
```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
ARTIFACT_DIR=$(awk -F= '$1=="artifact_dir"{print $2}' "$REPO_ROOT/.prforge-run" 2>/dev/null | tail -1)
test -n "$ARTIFACT_DIR"
export ARTIFACT_DIR
```

```python
import subprocess, json
from pathlib import Path

git_name = subprocess.check_output(['git', 'config', 'user.name'], text=True).strip()
git_email = subprocess.check_output(['git', 'config', 'user.email'], text=True).strip()

if not git_name or not git_email:
    print("WARNING: git user.name or user.email not configured.")
    print("Set with: git config --global user.name 'YourName'")
    print("          git config --global user.email 'your@email'")

state_path = Path(__import__('os').environ['ARTIFACT_DIR']) / 'state.json'
state = json.load(open(state_path))
state['git_identity'] = {
    'name': git_name,
    'email': git_email,
    'configured': bool(git_name and git_email)
}
open(state_path, 'w').write(json.dumps(state, indent=2))
```

**CRITICAL: `git_identity` is the ONLY source of truth for commit authorship.**
- `git config user.name` / `git config user.email` → commit author
- `github_user` (from `gh api user`) → ownership checks ONLY
- NEVER construct a commit email from the GitHub username
- NEVER use `users.noreply.github.com` addresses unless that IS the configured git email
- If `git_identity.configured` is false, STOP and ask the user to configure git before proceeding
- The agent must NOT override `git config` values with GitHub-derived values

**Permanently exclude `.prforge/` from git — MUST be the first git operation on any activation:**
```bash
# Only use .git/info/exclude — never modify .gitignore for PRForge artifacts.
# .gitignore modifications are tracked changes that get pushed upstream.
# .git/info/exclude is local-only and never committed.
EXCLUDE_FILE="$REPO_ROOT/.git/info/exclude"
if ! grep -qF ".prforge/" "$EXCLUDE_FILE" 2>/dev/null; then
  echo ".prforge/" >> "$EXCLUDE_FILE"
fi
if ! grep -qF ".prforge-run" "$EXCLUDE_FILE" 2>/dev/null; then
  echo ".prforge-run" >> "$EXCLUDE_FILE"
fi
if ! grep -qF ".prforge-*" "$EXCLUDE_FILE" 2>/dev/null; then
  echo ".prforge-*" >> "$EXCLUDE_FILE"
fi
```
This is not optional. Run it before touching any other file. If `.prforge/` ever
appears in `git status` output as tracked or staged, it is a hard stop — remove it
from the index immediately before doing anything else.

**NEVER modify `.gitignore`** to add PRForge patterns. That creates a tracked change
that would be pushed upstream, advertising the use of an AI harness to the repo.

Before any edits, run and record:

```bash
git status --short
git branch -vv
git remote -v
git log --oneline --decorate -8
git diff --stat
```

Save to `$ARTIFACT_DIR/snapshots/preflight.patch`.

If dirty tree exists, classify:
- **User changes** — uncommitted work that isn't part of this PR. Stash or warn.
- **Existing PR changes** — previous PRForge work. Safe to build on.
- **Generated artifacts** — files under `$ARTIFACT_DIR`. Safe.
- **Unknown** — stop and ask the user before proceeding.

---

## Phase 1: INTAKE

### Step 1: Identify the user

```bash
GITHUB_USER=$(gh api user --jq '.login' 2>/dev/null || echo "unknown")
```

Record `github_user` in `$ARTIFACT_DIR/state.json` and `$ARTIFACT_DIR/task.json`. This is used throughout the
run to determine ownership and to enforce the no-coauthor rule.

### Step 2: Normalize the task

Normalize the input into a `task.json`:

```json
{
  "type": "review_response",
  "source_url": "https://github.com/org/repo/pull/123#discussion_r...",
  "repo": "org/repo",
  "local_path": "/home/bamn/repo",
  "github_user": "bamn",
  "objective": "Address maintainer review comments on timeout override PR",
  "required_items": [],
  "optional_items": []
}
```

Detect:
- Repo identity (`gh repo view` or `git remote get-url origin`)
- Current branch and base branch
- Remotes (origin = fork, upstream = original)
- PR author (to confirm user ownership)
- Intelligence mode (see below)
- **Branch/base drift** (Guard #3, see below)

### Intelligence Mode Detection and Index Freshness (Guard #10)

1. Check if GitNexus MCP tools are available (test: `mcp__gitnexus__list_repos({})`)
2. **Strict Index Freshness Check:** If GitNexus is available, read `.gitnexus/meta.json` and compare its `lastCommit` field against `git rev-parse HEAD`.
   - If the index is stale (commits don't match): you MUST either run `npx gitnexus analyze --embeddings` to refresh it automatically, or if unable, record the state as `STALE_INDEX`, set `minimum_risk_floor: high`, and include a disclosure that the intelligence may be hallucinating history.
3. Check `gh auth status` - Note: This check must be performed whenever making API calls across the PRForge lifecycle, as auth tokens may expire mid-session.
4. Set mode in `state.json`:
   - `full_gitnexus` — GitNexus available and repo indexed (and fresh)
   - `degraded_gh` — GitNexus unavailable or stale, `gh` CLI available
   - `degraded_local` — Only local git/rg available

**If GitNexus is unavailable or stale**, record in `state.json` `intelligence`:
- `unavailable_capabilities`: list specific capabilities that were NOT available (e.g. `prior_PR_analysis`, `semantic_search`, `maintainer_history`, `cross_repo_similarity`)
- `minimum_risk_floor`: set to `medium` unless repo intelligence was clearly sufficient from local/gh sources
- `disclosure`: plain-English explanation for the approval artifact

Example disclosure:
> GitNexus unavailable. Could not inspect prior semantic relationships, historical similar PRs, or maintainer feedback patterns. Fallback: rg, git log, gh PR search. Risk impact: Medium.

### Ownership Detection (Guard #9)

For PR links, determine ownership before proceeding:

```bash
PR_AUTHOR=$(gh pr view <pr_number> --json author --jq '.author.login' 2>/dev/null || echo "unknown")
BRANCH_HEAD=$(gh pr view <pr_number> --json headRepository --jq '.headRepository.nameWithOwner' 2>/dev/null || echo "unknown")
```

| Condition | Classification | Behavior |
|-----------|---------------|----------|
| `PR_AUTHOR == GITHUB_USER` | `confirmed_user` | Full workflow |
| `PR_AUTHOR != GITHUB_USER` AND `BRANCH_HEAD` contains `GITHUB_USER` | `ambiguous_fork` | Verify local repo/branch matches before proceeding |
| `PR_AUTHOR != GITHUB_USER` AND `BRANCH_HEAD` does not match | `confirmed_other` | Read-only mode — do NOT activate destructive workflow |
| Branch head repo unknown | `ambiguous_branch_mismatch` | Ask user before proceeding |

**If ownership is ambiguous**, enter read-only review mode. Present the review
decomposition and ask for explicit approval before activating the full workflow.
Do NOT auto-activate destructive operations on someone else's PR.

Record in `state.json` under `ownership`.

### Task Type Detection

| Input | Task Type |
|-------|-----------|
| GitHub issue link | `new_pr` |
| "find PR candidates" / "find issues I can fix" | `candidate_discovery` |
| GitHub PR link (confirmed user, has reviews) | `review_response` |
| GitHub PR link (confirmed user, no reviews / general polish) | `pr_polish` |
| GitHub PR link (confirmed NOT user) | `new_pr` (read-only) |
| Review link / pasted review comments | `review_response` |
| Failing CI log / "fix CI" | `ci_fix` |
| Local task description | `local_task` |

**Key rule:** If the user pastes their own PR link and it has review comments,
the task is ALWAYS `review_response`. Collect every single reviewer concern.
Nothing gets left unaddressed.

### Branch/Base Drift Detection (Guard #3)

Before any edits, verify the working branch is based on the expected upstream base:

```bash
git fetch upstream --prune 2>/dev/null || git fetch origin --prune

# Is the base branch an ancestor of HEAD?
git merge-base --is-ancestor upstream/main HEAD 2>/dev/null
ANCESTOR=$?

# How far behind/ahead?
BEHIND=$(git rev-list --count HEAD..upstream/main 2>/dev/null || echo 0)
AHEAD=$(git rev-list --count upstream/main..HEAD 2>/dev/null || echo 0)
```

Classify drift:

| Condition | Status | Action |
|-----------|--------|--------|
| `ANCESTOR == 0` AND `BEHIND == 0` | `base_current` | Proceed |
| `ANCESTOR == 0` AND `BEHIND > 0` | `base_behind_but_safe` | Warn user; proceed only if contract permits rebase |
| `ANCESTOR != 0` | `base_diverged_needs_rebase` | Block; require rebase before proceeding |
| Base branch doesn't match expected | `wrong_base_branch` | Block; wrong base |

**Do NOT auto-rebase** unless the contract explicitly permits it. Record in `state.json` under `branch_status`.

On activation, check for existing `.prforge/` and resume from the current phase
in `state.json` rather than starting over.

---

## PHASE EXIT GATE — INTAKE

Before advancing to INVESTIGATE, all of the following must be true:

- [ ] `github_user` recorded in `state.json` and `task.json`
- [ ] `task.json` written with type, source_url, repo, objective
- [ ] `.prforge/` excluded via `.git/info/exclude` (and `.gitignore` if present)
- [ ] Git hooks installed (pre-commit, commit-msg) if not already present
- [ ] `git_identity` (name, email, configured) recorded in `state.json`
- [ ] Ownership classification recorded in `state.json` under `ownership`
- [ ] Intelligence mode set in `state.json`
- [ ] **GitNexus Freshness Check:** If GitNexus is available, index freshness was verified against HEAD, or a `STALE_INDEX` disclosure is recorded.
- [ ] Branch/base drift status recorded in `state.json` under `branch_status`
- [ ] Preflight snapshot saved to `.prforge/snapshots/preflight.patch`
- [ ] Task type detected and mode playbook read (see kernel Sub-Document Loading)
- [ ] `state.json` phase updated to `INTAKE`
