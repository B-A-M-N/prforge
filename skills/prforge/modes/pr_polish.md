# Mode: pr_polish — Additional Instructions

Read this file immediately after detecting task type `pr_polish` in INTAKE.

---

## Schema Requirements for This Mode

At PLAN time, read and merge `$SKILL_ROOT/schemas/pr_polish.json` with
`$SKILL_ROOT/schemas/base.json`.

---

## PR Polish Mode (`pr_polish`)

When the user links their own open PR that has no review comments (or asks to "clean up" / "polish" a PR):

**Goal:** Improve the PR to maximize merge probability — tighten the PR body, verify test coverage,
check for scope creep, and confirm all validation claims are honest.

### Step 1: Fetch PR state and Code Context
```bash
gh pr view <pr_number> --json title,body,headRefName,baseRefName,files,commits,statusCheckRollup
```
Use `grep` and `find` to map out the relevant codebase related to the files changed in the PR to ensure full context before polishing. Run `mcp__gitnexus__list_repos({})` to gather broad repository intelligence.

### Step 2: Run the same hostile review checklist
Apply `references/hostile-review-checklist.md` to the existing diff. Log findings in `.prforge/hostile_review.md`. 
Ensure you identify every logical edge case, unhandled error, or missing test case as meticulously as in the `investigate.md` workflow.

### Step 3: Identify polish targets

| Issue | Action |
|-------|--------|
| PR body is vague or missing "how validated" section | Rewrite body using PR body template |
| Scope creep: files changed that aren't in PR title/description | Flag — user must decide to split or remove |
| Missing tests for changed source files | Add tests (same rules as IMPLEMENT phase) |
| CI checks failing (related) | Fix the failure — follow ci_fix workflow |
| Commit messages contain AI attribution or WIP labels | Alert user — suggest `git rebase -i` to amend |
| PR title is vague | Suggest a cleaner title |

### Step 4: Apply safe improvements
Only change what's clearly needed. Do not refactor, do not touch unrelated files.
Changes allowed in polish mode:
- PR body rewrite
- Adding missing tests
- Fixing a CI failure that's related to the PR's own changes

### Step 5: Produce approval artifact
Same as normal PACKAGE → APPROVAL flow. User must approve before any push or PR body update. You must include a full list of all `review_decomposition` and tests run in the `approval.md` file.
