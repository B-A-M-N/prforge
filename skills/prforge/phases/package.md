# Phase 7: PACKAGE

Read this file at the START of PACKAGE before doing any work.

---

Generate the final output artifacts.

## Coding Discipline

Companion plugins are optional inputs. PRForge policy gates are mandatory outputs.

1. If `andrej-karpathy-skills` is installed:
   - PACKAGE MUST include a mandatory discipline verdict in `approval.md`:
     - Status: PASS | WARNING | BLOCKED
     - Was the change surgical and minimal?
     - Were no unnecessary abstractions introduced?
     - Did every line map to the stated objective?
   - Discipline status BLOCKED BLOCKS `approval.md` generation and REDIRECTS to SELF_REVIEW.

2. If `andrej-karpathy-skills` is not installed:
   - PACKAGE MUST use PRForge's built-in `hostile_review.md` and `dod.md` for the same verdicts.
   - Absence of the external plugin MUST NOT weaken the discipline verdict.

3. PACKAGE cannot produce `approval.md` unless the discipline verdict is PASS or WARNING with justification.

## Guard #1: Review Freshness Check

Before packaging, re-fetch the PR review state:

```bash
gh api repos/{owner}/repo/pulls/{pr_number}/comments --paginate | jq length
gh api repos/{owner}/repo/pulls/{pr_number}/reviews --paginate | jq length
gh pr view <pr_number> --json statusCheckRollup
```

Compare against `state.review_freshness.comments_at_fetch` and
`state.review_freshness.last_fetched_at`.

If new reviewer comments or new check failures appeared since `last_fetched_at`:
- Update `state.review_freshness`:
  ```json
  {
    "new_comments_since_fetch": N,
    "new_check_failures_since_fetch": N,
    "fresh": false
  }
  ```
- Return to INVESTIGATE to classify new concerns
- Regenerate `review_decomposition.md`

If fresh:
```json
{ "fresh": true, "new_comments_since_fetch": 0, "new_check_failures_since_fetch": 0 }
```

## Guard #7: Scope Delta Check

Compare `contract.md` allowed files against actual changed files:

```bash
# Files changed between base and HEAD
git diff --name-only upstream/main...HEAD
```

Cross-reference with `contract.md` allowed files list.

Record in `state.json` under `scope.delta_check`:
```json
{
  "contract_files": ["path/from/contract"],
  "actual_changed_files": ["path/that/changed"],
  "unexpected_files": ["path/not/in/contract"],
  "scope_clean": true
}
```

If `unexpected_files` is non-empty:
- If the unexpected changes are clearly necessary (test files, types): update contract and note it
- If the unexpected changes are scope creep: remove them
- Block approval until `scope_clean` is true

## For New PRs: `pr_body.md`

**FINALITY RULE — commit hash at top of PR body:**

Include the HEAD commit hash at the top of every PR body. Reviewers can use it
to check out the exact state of the contribution, verify the diff, or reference
it in follow-up reviews.

```markdown
**Commit:** `<full-sha>` (`<short-sha>`)

## Summary
- Fixed [what]
- Added [what]
- Preserved [what]

## Why
[Motivation. Reference issue if applicable.]

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

Compute before generating the body:

```bash
CONTRIBUTING_HASH=$(git rev-parse HEAD)
SHORT_HASH=$(git rev-parse --short HEAD)
```

For multi-commit PRs, use the HEAD hash (latest commit). If the PR branch has
not been squashed and the diff spans many unrelated commits, consider a clean
rebase so the PR tells a coherent story with a meaningful HEAD hash.

## For Review Responses: `review_response.md`

**FINALITY RULE — MUST include commit hash at top of every response:**

At the very top of every review response, before any text, MUST include the git commit
hash that addresses the reviewed changes. This makes the reviewer's job easier —
they can `git show <hash>` or click the hash in the GitHub UI to see exactly
what changed in response to their feedback. Omitting the hash BLOCKS phase exit.

```markdown
# Maintainer Response Draft

**Commit:** `<full-sha>` (`<short-sha>`)

Thanks, agreed. [One sentence acknowledging the concern.]

[One to two sentences explaining what you changed and why.]

Validation:
- `npm test -- ...` — passed
- `npm run typecheck` — passed
```

Compute the commit hash as the HEAD of the working branch after all changes are
committed:

```bash
 CONTRIBUTING_HASH=$(git rev-parse HEAD)
 SHORT_HASH=$(git rev-parse --short HEAD)
```

If multiple commits address the review, use the latest (highest) commit hash.
If the changes span multiple commits and there is no single "response" commit,
squash or create a merge commit so there IS one clear hash, then reference that.

Do not post a review response without a commit hash at the top. If you cannot
produce a hash (no commits yet), commit first, then package.

**Tone rules:**
- No defensiveness
- No "the AI did it"
- No over-explaining
- No arguing unless strong evidence
- Always acknowledge valid concern first
- Be direct and professional

## Guard #5: Public Response Preview

If the approval will include posting any public text (review responses, PR body,
issue comments), the full exact text MUST be visible in `approval.md` and
`state.json`:

- `review_response.md` → `state.public_text.review_response`
- `pr_body.md` → `state.public_text.pr_body_update`
- Any issue comment → `state.public_text.issue_comment`

**No hidden generated response.** If text will be posted publicly, the user sees
the exact text in the approval artifact.

## Approval Fingerprint

Before presenting the approval, compute and record hashes in `state.json`:

```bash
DIFF_HASH=$(python3 - <<'PY'
import hashlib, subprocess
u = subprocess.run(["git", "diff", "--binary", "--full-index"], capture_output=True).stdout
s = subprocess.run(["git", "diff", "--cached", "--binary", "--full-index"], capture_output=True).stdout
print(hashlib.sha256(u + b"\0PRFORGE-STAGED\0" + s).hexdigest())
PY
)
VAL_HASH=$(sha256sum "$ARTIFACT_DIR/validation_ledger.md" | awk '{print $1}')
APPROVAL_HASH=$(sha256sum "$ARTIFACT_DIR/approval.md" | awk '{print $1}')
DOD_HASH=$(sha256sum "$ARTIFACT_DIR/dod.md" | awk '{print $1}')
```

Record in `state.json`:
```json
{
  "approval": {
    "approval_id": "<ISO timestamp>",
    "diff_hash": "<DIFF_HASH>",
    "validation_hash": "<VAL_HASH>",
    "approval_md_hash": "<APPROVAL_HASH>",
    "dod_hash": "<DOD_HASH>"
  }
}
```

---

## PHASE EXIT GATE — PACKAGE

Before advancing to APPROVAL, all of the following must be true:

- [ ] `approval.md` written in full — no placeholders
- [ ] All guards (1–10) have run and results recorded in `state.json`
- [ ] Approval fingerprint hashes computed and recorded
- [ ] `state.release.approval_status` set to READY_TO_SHIP, READY_WITH_WARNINGS, or BLOCKED
- [ ] `state.json` phase updated to `APPROVAL`

**You may not commit, push, post, or create a PR from this point.
Present the approval artifact, ask the explicit approval question, and stop.
Do not proceed until the user gives an explicit affirmative.**
