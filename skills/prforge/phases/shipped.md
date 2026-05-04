# Phase 9: SHIPPED

Read this file at the START of SHIPPED after user gives explicit approval.

---

## Idempotency Guard

**Before executing any shipping action**, verify all of the following from `.prforge/state.json`:

- [ ] `state.phase == "APPROVAL"` or `state.phase == "SHIPPED_PENDING"`
- [ ] `state.approval.approved == true`
- [ ] `state.approval.approval_id` exists and is non-empty
- [ ] `state.approval.consumed != true` — if `consumed == true`, **stop immediately** and report what was already shipped via `.prforge/shipping_ledger.json`
- [ ] `state.approval.diff_hash` matches current diff:
  ```bash
  git diff --stat 2>/dev/null | sha256sum | awk '{print $1}'
  # must equal state.approval.diff_hash
  ```
- [ ] `state.approval.validation_hash` matches current validation ledger:
  ```bash
  sha256sum .prforge/validation_ledger.md | awk '{print $1}'
  # must equal state.approval.validation_hash
  ```
- [ ] `state.release.suggested_actions` exists and is non-empty
- [ ] `.prforge/` is not tracked or staged:
  ```bash
  git ls-files .prforge/          # must return empty
  git status --short -- .prforge/ # must return empty
  ```
  If either returns output: **hard stop**. Run `git reset HEAD .prforge/` and `git rm --cached -r .prforge/` to clean, then re-verify before proceeding. Do not push with `.prforge/` in the index under any circumstances.

If any hash mismatches: diff or validation changed after approval. **Hard block** — return to PACKAGE, regenerate approval, get fresh approval before proceeding.

---

## Execution

The `/pr-approve` command verifies integrity and dispatches execution. Follow its instructions exactly.

**Execute ONLY actions listed in `state.approval.approved_actions`** using commands from `state.release.suggested_actions`. No improvised actions.

---

## Shipping Ledger

After each successful public action, append to `.prforge/shipping_ledger.json` (create if absent; append to array if exists — never overwrite):

```json
{
  "approval_id": "<state.approval.approval_id>",
  "action_type": "push | pr_create | review_comment | issue_comment | label | force_push",
  "command": "<exact command run>",
  "result": "success | failed",
  "url": "<PR URL, comment URL, or null>",
  "timestamp": "<ISO 8601 UTC>"
}
```

---

## Completion

After all approved actions complete, update state:

```json
{ "phase": "SHIPPED", "approval": { "consumed": true } }
```

Confirm to the user what was done:
- "Pushed to `origin/fix-branch-name`"
- "Created PR: https://github.com/org/repo/pull/N"
- "Posted review response to PR #N"

**Never execute shipping actions twice for the same `approval_id`.**
