# Mode: new_pr — Additional Instructions

Read this file immediately after detecting task type `new_pr`, `ci_fix`, or `local_task` in INTAKE.
This file supplements the phase playbooks — it does not replace them.

---

## When This Mode Activates

| Input | Task Type |
|-------|-----------|
| GitHub issue link | `new_pr` |
| GitHub PR link (confirmed NOT user) | `new_pr` (read-only) |
| Failing CI log / "fix CI" | `ci_fix` |
| Local task description | `local_task` |

---

## Mode-Specific INVESTIGATE Requirements

For issues, the INVESTIGATE phase should gather:

```bash
# Fetch issue details
gh issue view <issue_number> --repo <owner/repo> --json title,body,labels,comments,assignees,state

# Fetch maintainer comments on the issue
gh api repos/{owner}/{repo}/issues/{n}/comments

# Check for prior PRs that attempted to fix this issue
gh pr list --repo <owner/repo> --search "<issue keywords>" --state all --json number,title,state,mergedAt
```

Write `issue_analysis` to `state.json`:
```json
{
  "issue_number": 123,
  "issue_analysis": {
    "root_cause": "string",
    "acceptance_criteria": ["array"],
    "related_files": ["array"]
  }
}
```

---

## Mode-Specific PACKAGE Requirements

Generate `pr_body.md` — this is the full PR description that will be posted publicly:

```markdown
## Summary
- Fixed [what]
- Added [what]
- Preserved [what]

## Why
[Motivation. Reference issue if applicable: Fixes #N]

## What Changed
- `file.ts`: [what changed and why]
- `file.test.ts`: [what was tested]

## Validation
- `npm test -- ...` — passed (N tests)
- `npm run typecheck` — passed

## Scope
- Does not change [X]
- Does not alter [Y]
- Does not affect [Z]

## Risk / Compatibility Notes
- [Any risks, or "Low risk — isolated change with regression coverage"]
```

The full PR body text must be previewed in `approval.md` before posting.
Record in `state.public_text.pr_body_update`.

---

## ci_fix Specifics

For `ci_fix` tasks:
- Fetch the failing CI log before writing any code
- Classify the failure as related/unrelated to the PR's current diff
- If unrelated: document it and ask the user whether to address it
- If related: fix the root cause, not just the symptom
- After fixing, re-run the specific failing check locally if possible

---

## Schema Requirements for This Mode

At PLAN time, read and merge `$SKILL_ROOT/schemas/new_pr.json` with
`$SKILL_ROOT/schemas/base.json`. Additional required fields:

- `required_at_investigate`: `issue_analysis`
- `required_at_package`: `pr_body`

Verify `state.json` has all these fields before leaving each phase.
