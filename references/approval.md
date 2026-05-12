# Approval Request

**Approval ID:** `[ISO timestamp]`
**Generated at:** `[ISO timestamp]`
**Status:** `READY_TO_SHIP` | `READY_WITH_WARNINGS` | `BLOCKED`

> **INTEGRITY:** This approval is bound to a specific diff and validation state.
> If any code changed after this file was generated, this approval is STALE and must
> be regenerated. The `/pr-approve` command will verify this automatically.

---

## Result

I am ready to [push update / create PR / post review response].

## Plain-English summary

[Two to five sentences explaining what was fixed, in terms a busy person can read in 15 seconds.]

## What changed

- `[file]` — [what changed and why]
- `[file]` — [what changed and why]
- `[file]` — [test added/updated]

## What was NOT changed

- [Important scope boundary — what risky thing was intentionally avoided]
- [Any behavior that was preserved]

## Definition of Done

| Item | Status |
|------|--------|
| [Each item from dod.md, one row per item] | ✅ done / ❌ BLOCKED / ⚠️ warning |

**Unchecked items are blockers.** If any row shows ❌, approval status is BLOCKED.

## Scope check

- **Allowed files changed:** Yes
- **Unexpected files changed:** No
- **Scope clean:** Yes

## Blast radius

- **Score:** low / medium / high
- **Files changed:** [N]
- **Test coverage:** [N tests found / No tests found]
- **Dependents affected:** [N files import changed modules]
- **Direct Callers (d=1):** [List top 3-5 callers, or "None"]
- **Public API touched:** Yes / No

## Validation

**Passed:**
- `[command]` — [brief result]
- `[command]` — [brief result]

**Failed:**
- `[command]` — [brief result]
  - Reason: [why it failed and why it's acceptable / not-acceptable]
  - Related to changes: [Yes/No]
  - Current status: [blocked / acceptable-risk / needs-fix]

**Not run:**
- `[command]` — Reason: [why]

## Risk: Low / Medium / High

[One or two sentences. If medium or high, explain the specific remaining concern.]

## GitHub checks

**Status:** passed | failed_related | failed_unrelated | pending | unavailable

- `[check_name]` — [status] — [related to this PR: Yes/No] — [reason if unrelated]

## Review freshness

- **Last fetched:** [ISO timestamp]
- **New comments since fetch:** [N]
- **New check failures since fetch:** [N]
- **Fresh:** Yes / No

## Repo intelligence

- **GitNexus:** [available / unavailable]
- **Unavailable capabilities:** [list specific missing capabilities, or "None"]
- **Fallback used:** [local grep + gh CLI + package scripts / local-only, no gh]
- **Risk impact:** [None / Low / Medium / High]

## Branch status

- **Base branch:** [current / behind / diverged / wrong base]
- **Commits behind base:** [N]
- **Commits ahead of base:** [N]
- **Drift status:** [base_current / base_behind_but_safe / base_diverged_needs_rebase / wrong base]

## Ownership

- **PR author:** @[login]
- **Confirmed owner:** [Yes / No / Ambiguous]
- **Resolution:** [confirmed_user / confirmed_other / ambiguous_fork / ambiguous_branch_mismatch]

---

## Needs your decision

> The following reviewer comments could not be safely auto-fixed. They are surfaced here for your decision.

### D1 — [Short description]
- **Reviewer:** @reviewer_login
- **Original comment:** "[Exact or paraphrased quote]"
- **What they seem to be suggesting:** [Plain-English interpretation]
- **My recommendation:** [What you think should happen / "Defer to you — this is a product/architecture decision"]
- **If ignored:** [What happens — e.g., "May result in a follow-up review comment" / "Low risk — can be addressed in a future PR"]

---

## Public text to be posted

> The following text will be posted publicly. Review it carefully.

### Review response
```
[Exact text that will be posted as the review response]
```

### PR body (if creating/updating)
```
[Exact PR body text]
```

---

## Approval covers

Check exactly what you are approving:

- [ ] **Push branch** — `git push origin [branch]`
- [ ] **Force-push branch** — `git push --force-with-lease origin [branch]`
- [ ] **Create PR** — with title: `[title]`
- [ ] **Post review response** — [brief description of what will be posted]
- [ ] **Request review** — from: [reviewers]

> Only checked actions will be executed. Nothing else.

---

## Integrity fingerprint

```
diff_hash:        [SHA256 of git diff --stat at approval time]
validation_hash:  [SHA256 of validation_ledger.md at approval time]
approval_hash:    [SHA256 of this file at approval time]
```

If any of these don't match at execution time, the approval is stale and will be rejected.

---

## Your options

- **Approve** — Execute exactly the checked actions above
- **Request changes** — Tell me what to adjust, I'll re-run and regenerate
- **Stop** — Abort, preserve all local changes for manual work
