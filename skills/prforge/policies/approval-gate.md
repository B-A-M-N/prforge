# Policy: Approval Gate

Read this file at activation. Always active — applies to every mode.

---

## Rule 7: Never publish without approval

Push, PR creation, posting review comments — ALL require explicit user sign-off.

PRForge may modify code, run commands, amend local commits, and prepare responses.

**PRForge may NOT, without user approval:**
- Push to any remote
- Force-push (even with --force-with-lease)
- Create a PR
- Post review comments
- Request review
- Change labels or assignees
- Close or reopen issues

This is enforced by the APPROVAL phase gate. No action in this list may occur
before the user gives an explicit affirmative response to the approval question.

## Rule 11: Never ship with BLOCKED approval status

If `state.release.approval_status == "BLOCKED"`, the approval artifact must
clearly show the blocking reason and the action must not proceed.

Blocking conditions that cannot be overridden:
- Validation failed in touched area (`validation.touched_area_failed == true`)
- Scope is dirty (`scope.delta_check.scope_clean == false`)
- Review is stale (`review_freshness.fresh == false`)
- `.prforge/` artifacts are staged or tracked
- DoD has unchecked required items

Fix the blocking condition, regenerate the approval, then ask again.

## Rule 13: Never hide public text

If text will be posted publicly (review responses, PR body updates, issue comments),
the exact text MUST be visible in the approval artifact BEFORE posting.

No generated response may be posted without the user seeing it first, verbatim:
- `review_response.md` → `state.public_text.review_response`
- `pr_body.md` → `state.public_text.pr_body_update`
- Any issue comment → `state.public_text.issue_comment`

## What Counts as Explicit Approval

The user must give an explicit affirmative response to the approval question.

**These count:**
- "yes"
- "go ahead"
- "push it"
- "looks good"
- "ship it"
- "do it"

**These do NOT count:**
- User silence after a summary
- User asking a follow-up question
- User saying "that looks right" mid-conversation without responding to the approval question
- User trailing off after the summary

The approval question must be a direct question naming the pending action (branch, remote, PR number).
Generic questions ("should I proceed?") are not sufficient.

## Approval Fingerprint Integrity

Before any approved public action executes, the `/pr-approve` command verifies:
- `DIFF_HASH` matches current `git diff --stat` (diff hasn't changed since approval)
- `VAL_HASH` matches current `validation_ledger.md` (ledger wasn't modified)
- `APPROVAL_HASH` matches current `approval.md` (approval artifact wasn't modified)

If any hash mismatches: the approval is stale. Regenerate and re-ask.

## Finality Rule — Commit Hash in Every Public Response

**Every** public-facing text — PR bodies, review responses, follow-up comments —
MUST include the contributing commit hash at the very top. This is non-negotiable.

```
 format:
**Commit:** `<40-char-sha>` (`<short-sha>`)

 Example:
**Commit:** `a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2` (`a1b2c3d`)
```

**Why:** Reviewers can `git show <hash>` to see exactly what changed. They can
click the hash in the GitHub UI. They can reference it in subsequent reviews.
Without it, the reviewer has to hunt for which commits correspond to this response.

**When:**

- PR body: hash of HEAD at time of PR creation
- Review response: hash of the commit(s) that address the review
- Follow-up comments on subsequent reviews: hash of the new commits

**Rules:**

- Use the full 40-character SHA, plus the short SHA in parentheses.
- If multiple commits address feedback, use the latest (HEAD) commit.
- If changes are unsquashed and the hash story is messy, squash first, then post.
- Compute BEFORE generating the text: `git rev-parse HEAD`
- Do NOT post any public text that lacks the commit hash.
- This applies in local mode AND distributed worker mode.
- In distributed revision jobs: use the commit produced by the revision work.
