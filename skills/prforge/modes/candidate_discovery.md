# Mode: candidate_discovery — Additional Instructions

Read this file immediately after detecting task type `candidate_discovery` in INTAKE.

---

## Candidate Discovery Mode

When the user asks to find PR candidates for a named repo (no specific issue/PR link):

### Step 1: Gather repo health signals

```bash
# Open issues with good-first-issue or help-wanted labels
gh issue list --repo <owner/repo> --label "good first issue" --label "help wanted" --state open --json number,title,labels,createdAt,comments,assignees | head -30

# Recent PR merge rate (maintainer responsiveness)
gh pr list --repo <owner/repo> --state merged --json mergedAt,number | head -20

# Has CONTRIBUTING.md?
gh api repos/<owner/repo>/contents/CONTRIBUTING.md --jq '.name' 2>/dev/null || echo "none"

# Is the issue already assigned or claimed?
# (check assignees field from issue list above)
```

### Step 2: Fetch open issues and classify by type

For each open issue, classify the PR type:

| Type | Signal words / labels |
|------|-----------------------|
| `bug` | "fix", "broken", "regression", "error", "crash", labels: bug |
| `feature` | "add", "support", "implement", labels: enhancement, feature |
| `docs` | "docs", "documentation", "readme", "example", labels: documentation |
| `auth/oauth` | "auth", "oauth", "token", "credential", "permission" |
| `integration` | "integration", "provider", "plugin", "connector", "adapter" |
| `test` | "test", "coverage", "spec", labels: test |
| `perf` | "slow", "performance", "optimize", "memory", "latency" |
| `refactor` | "refactor", "cleanup", "simplify", "extract", "decouple" |
| `type/lint` | type errors, lint warnings, "types", "TypeScript" |

### Step 3: Score each candidate

Score each issue on:
- **Scope size** — how many files would change (small = better)
- **Testability** — can it be validated locally without a production environment
- **Maintainer acceptance** — is there ≥1 maintainer comment agreeing it's valid?
- **Dependency risk** — does it touch deps, public APIs, or auth paths
- **Blast radius** — how much code is affected
- **Reproducibility** — can the bug/issue be reproduced from description alone
- **Achievability** — does the issue have a clear, agreed-upon solution direction?
- **Repo responsiveness** — recent merged PRs = maintainer is active (stale repos waste time)
- **Not claimed** — no assignee, no recent "I'll work on this" comment

**Automatically exclude:**
- Issues assigned to someone else
- Issues with "needs decision" / "blocked" / "wontfix" labels
- Issues requiring access to maintainer infrastructure (internal services, prod data)
- Issues open > 2 years with no maintainer engagement
- Issues requiring product/architecture decisions per maintainer comments (e.g. "should we redesign X?" — not the same as "this auth path has a bug")

**Topic area (auth, oauth, integration, etc.) is NOT a reason to exclude.**
What matters is scope clarity, solution consensus, and local testability — not the domain.
An auth bug with a clear repro and agreed fix is better than a vague "refactor" with no direction.

### Step 4: Present by type, ranked within each type

```
## Bug Fixes
  ✅ BEST  #N — [Title]
    One isolated function, clear repro, existing test file to extend. Maintainer confirmed valid.
    Scope: ~2 files. Testable locally.

  ⚠️  RISKY #N — [Title]
    Touches auth path. Maintainer hasn't commented in 3 months.

## Integration
  ✅ BEST  #N — [Title]
    New provider following an existing pattern (3 similar PRs merged). Template to copy.

## Docs
  ✅ EASY  #N — [Title]
    Missing example in README. Zero code risk.

## Auth/OAuth
  ✅ BEST  #N — [Title]
    Clear bug in token refresh path. Maintainer confirmed and left a hint. Existing test suite covers the area.

  🚫 AVOID #N — [Title]
    Requires product decision on token storage architecture. Unresolved maintainer debate.
```

5. Wait for user selection. Do not auto-pick.
6. After the user selects a candidate, initialize the state and transition to INTAKE:
   - Set `task.type` to the appropriate type for the selected issue (e.g. `new_pr`, `issue_fix`)
   - Set `task.source_url` to the selected issue URL
   - Set `task.objective` to a one-sentence description of the issue
   - Write `state.json` with phase `INTAKE` and the above task fields. **MANDATORY GATE:** You must completely drop `candidate_discovery` as the task type. If `task.type` remains `candidate_discovery`, the preflight hooks will reject the transition.
   - Continue with the normal `/pr` workflow from Phase 1 (INVESTIGATE) forward
   - Do NOT re-run candidate_discovery scoring; the selection is already made
