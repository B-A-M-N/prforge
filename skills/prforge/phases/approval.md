# Phase 8: APPROVAL (Release Gate)

Read this file at the START of APPROVAL before doing any work.

---

This is the only phase where you stop and wait for the user.

## Step 1: Determine Approval Status (Guard #8)

Before generating the approval, compute the overall readiness status:

**`BLOCKED`** if any of:
- `validation.touched_area_failed == true` (validation failed in changed files)
- `scope.delta_check.scope_clean == false` (unexpected files changed)
- `review_freshness.fresh == false` (new comments since last fetch)
- `ownership.ambiguous == true`
- `artifact_exclusion.clean == false`
- `blast_radius.tests_found_count == 0` AND `blast_radius.changed_files_count > 0` (no tests for changed files)
- Any changed source file has a test file that was NOT updated (stale tests)
- `.prforge/dod.md` has any unchecked item that is not an optional suggestion

**`READY_WITH_WARNINGS`** if any of:
- `ci_status.overall` is `ci_failed_related` or `ci_pending`
- `branch_status.drift_status` is not `base_current`
- GitNexus unavailable and `intelligence.minimum_risk_floor` is `medium` or higher
- Any validation command was not run
- There are `needs_user_decision` items
- `blast_radius.score` is `medium` or `high`
- `blast_radius.public_api_touched` is `true`

**`READY_TO_SHIP`** if none of the above apply.

Record in `state.release`:
```json
{
  "approval_status": "READY_TO_SHIP | READY_WITH_WARNINGS | BLOCKED",
  "blocking_reasons": ["..."],
  "warning_reasons": ["..."]
}
```

**Failed validation in touched area cannot be buried.** If
`validation.touched_area_failed == true`, the approval MUST be `BLOCKED` and
cannot present as "ready to ship."

## Step 2: Generate approval.md

Write `.prforge/approval.md` using the template from `references/approval.md`.

Fill in every section. Do not leave placeholders.

**Critical sections:**
- **Plain-English summary** — 2-5 sentences a busy person reads in 15 seconds
- **Repo intelligence disclosure** — state GitNexus availability, fallback used, and unavailable capabilities
- **GitHub checks** — CI status with related/unrelated classification
- **Review freshness** — last fetch time, new comments since fetch
- **Scope check** — allowed files vs actual changed files
- **Approval status** — `READY_TO_SHIP` / `READY_WITH_WARNINGS` / `BLOCKED`
- **Needs your decision** — any `needs_user_decision` items from review decomposition
- **Public text preview** — exact text of any response that will be posted
- **Approval checkboxes** — exactly which actions are being approved

## Step 3: Compute approval fingerprint

Before presenting the approval, compute and record hashes:

```bash
DIFF_HASH=$(git diff --stat 2>/dev/null | sha256sum | awk '{print $1}')
DIFF_HASH="$DIFF_HASH$(git diff --cached --stat 2>/dev/null | sha256sum | awk '{print $1}')"
VAL_HASH=$(sha256sum .prforge/validation_ledger.md | awk '{print $1}')
APPROVAL_HASH=$(sha256sum .prforge/approval.md | awk '{print $1}')
```

## Step 4: Record approval in state.json

```json
{
  "phase": "APPROVAL",
  "approval": {
    "approval_id": "<ISO timestamp>",
    "approved_at": "<ISO timestamp>",
    "approved_actions": ["force_push", "post_comment"],
    "diff_hash": "<DIFF_HASH>",
    "validation_hash": "<VAL_HASH>",
    "approval_md_hash": "<APPROVAL_HASH>",
    "stale": false
  }
}
```

## Step 5: Present the approval

Present `approval.md` to the user. Keep it scannable. The user should understand
what they're approving in 15 seconds.

Do NOT bury the approval in logs. Put detailed logs in `.prforge/` artifacts the
user can inspect if they want.

**After presenting the approval artifact, close with an explicit question.** Do not
trail off or wait silently. The final line must be a direct question that names the
pending action. Examples:

- "Is this good to push to `origin/fix-branch-name` and open the PR?"
- "Should I push this and post the review response to PR #42?"
- "Ready to push `origin/fix-branch-name` — does this look good to you?"

Adapt the question to the actual branch, remote, and action. Never generic.
Never omit it.

**Wait for explicit user approval.** Do not proceed until the user says yes.
