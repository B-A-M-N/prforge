# Hostile Review Checklist

Answer each question honestly. If the answer is "no" or "unclear," fix before proceeding.
Do not rationalize. If you wouldn't accept this PR from a stranger, fix it.

## Correctness
- [ ] Does this solve the actual problem stated in the contract?
- [ ] Does this handle the edge cases a maintainer would think of?
- [ ] Are there alternate code paths (auth types, provider configs, flags) that this might break?
- [ ] Did I introduce any new failure modes?

## Scope
- [ ] Did I touch only files allowed by the contract?
- [ ] Did I avoid unrelated cleanup or "while I'm here" changes?
- [ ] Did I avoid dependency changes?
- [ ] Is the diff small enough to review in 5 minutes?

## Precedence & Compatibility
- [ ] Did I preserve existing behavior for all non-target cases?
- [ ] Did I preserve existing config/provider/precedence chains?
- [ ] Did I avoid changing public API signatures?
- [ ] Did I check that default behavior is unchanged?

## Tests (required — any "no" must be resolved before proceeding)
- [ ] **Every changed source file has a corresponding test file** (new or updated)
- [ ] **Test files were actually updated** — not just that they exist (check `git diff` on test files)
- [ ] No changed source file is without test coverage (unless documented justification)
- [ ] Did I add tests near existing test files (not in random locations)?
- [ ] Do my tests follow the existing test style/patterns?
- [ ] Are my tests not brittle (not testing implementation details)?
- [ ] Do my tests actually fail without the fix confirmed?
- [ ] Did I cover the edge cases the maintainer called out?
- [ ] **No stale tests** — if a test file exists for a changed source, it was updated too

> If any test item is "no": **add the missing test yourself** before advancing. Only escalate to
> the user if the test framework requires production credentials, secrets, or infrastructure that
> isn't available locally — in that case, document the justification and note it in `approval.md`.

## Validation Honesty
- [ ] Did I actually run the validation commands?
- [ ] Did I record the real output, not what I expected?
- [ ] Did I mark un-run commands with reasons?
- [ ] Does the PR body match what was actually validated?

## Git Safety
- [ ] Is my branch tracking the correct remote (origin/fork)?
- [ ] Am I pushing to my fork, not upstream?
- [ ] Is my commit history clean and logical?
- [ ] Did I avoid force-push (or justified it if needed)?

## Maintainer Perception
- [ ] Would a maintainer consider this PR small and reviewable?
- [ ] Does the PR body clearly explain what, why, and how it was validated?
- [ ] Did I avoid defensive or over-explaining language?
- [ ] If this were my repo, would I merge this PR?

## Review Response Quality (if review_response mode)
- [ ] Did I address every required change from the review?
- [ ] Did I remove changes the maintainer flagged as out of scope?
- [ ] Is my response tone professional (not defensive, not needy)?
- [ ] Did I acknowledge valid concerns before explaining changes?
- [ ] Did I avoid arguing unless there is strong evidence?
