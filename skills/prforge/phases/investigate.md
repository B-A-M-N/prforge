# Phase 2: INVESTIGATE

Read this file at the START of INVESTIGATE before doing any work.

---

Gather repo intelligence. This is not decorative — the implementation must use it.

## Step 1: Detect Available Intelligence Sources (MANDATORY)

**You must actively probe for available tools. Do not assume. Do not rely solely on hooks.**

### Probe GitNexus
Call `mcp__gitnexus__list_repos({})`. If it returns without error: **GitNexus is available**.
Record in `state.intelligence.gitnexus_available = true`.

### Probe context-mode
Call `mcp__plugin_context-mode_context-mode__ctx_search` with a trivial query. If it returns without error: **context-mode is available**.
Record in `state.intelligence.context_mode_available = true`.

Record the determined intelligence mode in `state.intelligence.mode`:
- Both available → `full_gitnexus`
- GitNexus only → `gitnexus_only`
- context-mode only → `context_mode_only`
- Neither → `degraded_local`

Also record deterministic phase-gate evidence:

```json
{
  "intelligence": {
    "gitnexus_probe_attempted": true,
    "gitnexus_available": true,
    "evidence": {
      "gitnexus_calls": ["list_repos"],
      "primary_target": "path/or/symbol",
      "key_symbol": "symbol_or_component",
      "repo_intelligence_path": "<artifact_dir>/repo_intelligence.md"
    }
  }
}
```

---

## Step 2: Gather Intelligence (MANDATORY — use the best available source)

**Do not skip this step. Do not proceed to PLAN without completing it.**

### If GitNexus is available — MUST call all of these:

```
mcp__gitnexus__query({query: "<task description>", repo: "<owner/repo>"})
  → Find files, symbols, patterns related to the task

mcp__gitnexus__impact({target: "<primary file being changed>", direction: "both", repo: "<owner/repo>"})
  → Blast radius: what depends on this file, what it depends on

mcp__gitnexus__context({name: "<key symbol or function being changed>", repo: "<owner/repo>"})
  → Callers, callees, tests, usage patterns for the symbol

mcp__gitnexus__detect_changes({scope: "working_tree"})
  → Map current diff to affected symbols (if applicable)
```

Record all results in `.prforge/repo_intelligence.md` under `## GitNexus Intelligence`.

Record successful calls in `state.intelligence.evidence.gitnexus_calls`:

```json
["list_repos", "query", "impact", "context", "detect_changes"]
```

### If context-mode is available — MUST call:

```
mcp__plugin_context-mode_context-mode__ctx_search(queries: [
  "<task description>",
  "<primary file or symbol>",
  "<related test patterns>"
])
```

Record results under `## Context-Mode Intelligence`.

### If neither is available — degraded fallback (MUST still run):

```bash
# Related files
rg -l "<key symbol or pattern>" --type-add 'src:*.{ts,js,py,go,rs}' -t src

# File history
git log --oneline --all -- <primary_file> | head -20

# Related PRs / issues
gh pr list --repo <owner/repo> --search "<key term>" --state all --limit 10
gh issue list --repo <owner/repo> --search "<key term>" --limit 10

# Test discovery
find . -name "*.test.*" -o -name "*.spec.*" | xargs grep -l "<key symbol>" 2>/dev/null | head -10

# CI/build commands
cat package.json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('scripts',{}), indent=2))"
cat Makefile 2>/dev/null | grep "^[a-z]" | head -20
```

Record degradation in `state.intelligence`:
```json
{
  "mode": "degraded_local",
  "gitnexus_probe_attempted": true,
  "gitnexus_available": false,
  "unavailable_reason": "GitNexus MCP call failed or server not registered",
  "minimum_risk_floor": "medium",
  "unavailable_capabilities": ["symbol_graph", "blast_radius", "maintainer_history", "cross_repo_similarity"],
  "evidence": {
    "fallback_commands": [
      "rg -l <key symbol>",
      "git log --oneline --all -- <primary_file>",
      "gh pr list --search <key term>"
    ]
  }
}
```

The `INVESTIGATE -> PLAN` phase gate is redirective and deterministic. It will
redirect to intelligence repair instead of allowing PLAN if this evidence or
`repo_intelligence.md` is missing. A redirect is not task failure; complete the
missing intelligence evidence and retry the phase transition.

---

## Step 3: Blast Radius

If the `blast-radius.sh` hook has already run (check `state.blast_radius` exists), use those results.

If not, manually compute:
```bash
git diff --name-only HEAD                    # files changed so far
git diff --name-only upstream/main...HEAD    # files changed vs base
```

Cross-reference against `contract.md` allowed files. Record scope cleanliness in `state.scope.delta_check`.

---

## Source Priority Reference

| Source | What it provides |
|--------|-----------------|
| **GitNexus MCP** (`mcp__gitnexus__*`) | Symbol graph, blast radius, callers/callees, maintainer history, cross-repo similarity — **use first** |
| **context-mode MCP** | Codebase search, references, definitions, test runner, linter — **use second** |
| **gh CLI** | PR reviews, comments, CI checks, issue threads — always use for GitHub data |
| **firecrawl skill** | External docs, changelogs, CONTRIBUTING.md if not readable raw — use `Agent` tool with `skill: "firecrawl"` |
| **Local fallback** | `rg`, `find`, `git log` — always available, use when MCP unavailable |

**Never use firecrawl to scrape GitHub pages** — `gh` CLI is faster and authenticated.
**Never skip intelligence gathering** — if all sources fail, record why and set `minimum_risk_floor: high`.

## Output: `repo_intelligence.md`

```markdown
# Repo Intelligence

## Repo
- **Name:** org/repo
- **Branch:** working → base
- **Intelligence Mode:** full_gitnexus | degraded_gh | degraded_local

## Relevant Files
- `path/to/file.ext` — what it does, why it matters

## Prior Related PRs/Issues
- #N — brief description and outcome

## Known Constraints
- Do not alter X behavior
- Maintainer previously requested Y

## Likely Tests
- `path/to/test.ext` — what it covers
- Command: `npm test -- ...`

## Validation Commands
- `npm test -- <target>` — targeted tests
- `npm run typecheck` — type checking
- `npm run lint` — linting

## Maintainer Patterns (if GitNexus available)
- Prefers small, focused PRs
- Rejects unrelated cleanup
```

---

## For Review Response Mode — Mandatory Review Collection Pass

**This is a required gate. Nothing gets left unaddressed.**

When the task type is `review_response`, you MUST fetch and record every single
concern raised by every reviewer. This is not optional. The user is handing you
their reputation — you do not get to decide which feedback matters.

### Step 1: Fetch ALL review data

```bash
# Get the PR author, reviewers, and review states
gh pr view <pr_number> --json author,reviewRequests,reviews,comments,reviewThreads,state

# Get ALL review comments (inline + general)
gh api repos/{owner}/repo/pulls/{pr_number}/comments --paginate
gh api repos/{owner}/repo/pulls/{pr_number}/reviews --paginate

# Get review threads (GitHub's grouped conversations)
gh api repos/{owner}/repo/pulls/{pr_number}/comments --jq '.[] | {user: .user.login, body: .body, path: .path, line: .line, created_at: .created_at, in_reply_to_id: .in_reply_to_id}'
```

Also check:
- PR description for any "Changes requested" summary
- CI check results (failed checks are implicit required changes)
- Any "outdated" comments — they still count if not explicitly resolved

### Step 2: Classify EVERY concern

For EACH piece of reviewer feedback, classify:

| Classification | Meaning | Action |
|---------------|---------|--------|
| `blocker` | Must fix, prevents merge | Required code change |
| `required_change` | Must fix, maintainers won't merge without it | Required code change |
| `maintainer_preference` | Strong suggestion, likely to block if ignored | Required code change (unless you have strong evidence against it — document why) |
| `scope_reduction` | Remove something from the PR | Required removal/narrowing |
| `optional_suggestion` | Nice to have | Address unless it: expands PR scope, changes product/API behavior, requires architecture decisions, conflicts with another maintainer comment, or would weaken the original fix. If not addressed, record why in `review_decomposition.md` and `approval.md`. |
| `misunderstanding` | Reviewer may be wrong | Address with code change OR clear explanation in response |
| `needs_user_decision` | Agent cannot safely infer intent | Do NOT auto-fix. Surface in `approval.md` under "Needs your decision" with the original comment and a plain-English summary of what the maintainer seems to be suggesting. |
| `already_addressed` | Already fixed in a later commit | Note it, verify it's actually fixed |

**Default to treating reviewer feedback as required for triage purposes.** Address
optional-looking feedback unless it would meaningfully expand scope or requires a
product/architecture decision the agent is not qualified to make.

**Use `needs_user_decision`** for comments where intent is ambiguous or the suggestion
would change the nature of the fix. Examples:
- "Maybe we should support this differently?"
- "Should this be configurable?"
- "Can we align this with the new architecture?"

These should never be auto-fixed. They belong in the approval artifact for the
user to decide.

### Step 3: Record as required conditional gates

Every concern becomes a required item in the task queue. Record in
`review_decomposition.md`:

```markdown
# Review Decomposition

## PR: [title] — #[number]
## Reviewers: [list of reviewer logins]
## Review State: [approved / changes_requested / commented]

## Required Changes (ALL must be addressed)

### R1 — [Short description of concern]
- **Reviewer:** @reviewer_login
- **Type:** blocker | required_change | maintainer_preference
- **Original comment:** "[Exact or paraphrased quote]"
- **Action:** [Specific code/test/doc change needed]
- **Files likely affected:** [paths]
- **Status:** pending

### R2 — [Short description]
- **Reviewer:** @reviewer_login
- **Type:** scope_reduction
- **Original comment:** "[Exact or paraphrased quote]"
- **Action:** [What to remove/narrow]
- **Status:** pending

## Optional Suggestions (address unless scope risk)

### O1 — [Short description]
- **Reviewer:** @reviewer_login
- **Type:** optional_suggestion
- **Original comment:** "[Exact or paraphrased quote]"
- **Action:** [What could be improved]
- **Status:** pending
- **If not addressed:** [Reason — scope change / API behavior / architecture decision / conflict / would weaken fix]

## Needs Your Decision (do NOT auto-fix — surface for user)

### D1 — [Short description]
- **Reviewer:** @reviewer_login
- **Type:** needs_user_decision
- **Original comment:** "[Exact or paraphrased quote]"
- **What they seem to be suggesting:** [Plain-English interpretation]
- **Why this wasn't auto-fixed:** [Reason — ambiguous intent / requires product decision / would change fix scope]
- **Recommended action:** [What you think should happen, or "Defer to user"]

## Task Queue (ALL required items must be complete before packaging)
- [ ] R1: [description]
- [ ] R2: [description]
- [ ] O1: [description] — optional
- [ ] D1: [description] — needs user decision (will be surfaced in approval.md)

## Coverage Check
- Total reviewer concerns found: N
- Classified as required: N
- Classified as optional: N
- Needs user decision: N
- Already addressed: N
- **All concerns recorded:** YES / NO
```

### Step 4: GitHub CI/Check Status (Guard #2)

Fetch and classify CI/check status for the PR:

```bash
# Get check runs and their status
gh pr view <pr_number> --json statusCheckRollup --jq '.statusCheckRollup[] | {name: .name, status: .status, conclusion: .conclusion, startedAt: .startedAt}'

# Alternative: get detailed check runs
gh api repos/{owner}/repo/commits/{head_sha}/check-runs --paginate
```

Classify each check:

| Classification | Meaning |
|---------------|---------|
| `ci_passed` | All checks passing |
| `ci_failed_related` | Check failed AND relates to files changed in this PR |
| `ci_failed_unrelated` | Check failed but clearly unrelated (e.g. docs job failing on upstream main) |
| `ci_pending` | Checks still running |
| `ci_unavailable` | Cannot fetch check status |

For each failed check, determine if it's related to the PR's changes by:
- Checking if the check name relates to changed file paths
- Checking if the failure log mentions changed files
- If uncertain, classify as `ci_failed_related` (conservative)

Record in `state.json` under `ci_status`. This will be surfaced in the approval artifact.

### Step 5: Coverage verification

Before leaving INVESTIGATE, verify:
- Every reviewer comment has been read and classified
- No comment was skipped or ignored
- The task queue has an entry for every required concern
- The `review_decomposition.md` file is complete
- CI/check status has been fetched and classified
- Review freshness timestamp recorded (`state.review_freshness.last_fetched_at`)

**If you cannot fetch review data** (API errors, private repo, etc.), stop and tell
the user. Do not proceed with a partial understanding of the feedback.

---

## PHASE EXIT GATE — INVESTIGATE

Before advancing to PLAN, all of the following must be true:

- [ ] **Mode Re-validation:** Check `state.json`'s `task.type`. If it is `candidate_discovery`, abort INVESTIGATE immediately as mode was not correctly transitioned during Intake.
- [ ] `repo_intelligence.md` written with relevant files, prior PRs, known constraints, validation commands
- [ ] **GitNexus Evidence Check:** If `state.intelligence.gitnexus_available` is true, `state.intelligence.evidence.gitnexus_calls` MUST include "impact" and "context".
- [ ] **GitNexus Content Check:** If `state.intelligence.gitnexus_available` is true, `repo_intelligence.md` MUST contain a `## GitNexus Intelligence` section and a `## GitNexus Impact` section with non-placeholder content detailing the blast radius.
- [ ] Intelligence mode recorded in `state.json`
- [ ] `state.blast_radius` initialized (will be updated during IMPLEMENT)
- [ ] For `review_response` mode: `review_decomposition.md` complete, all comments classified, task queue populated
- [ ] For `review_response` mode: CI/check status fetched and classified in `state.ci_status`
- [ ] For `review_response` mode: `state.review_freshness.last_fetched_at` recorded
- [ ] `state.json` phase updated to `INVESTIGATE`
