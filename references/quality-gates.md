# PRForge Quality Gates

Each phase has mandatory entry criteria (must be true to enter) and exit criteria
(must be complete before advancing). The agent may NOT skip phases unless the user
explicitly invokes an emergency override.

All artifacts are stored in `$ARTIFACT_DIR` outside the target repo. Resolve it
from repo `.prforge-run`; see `references/artifact-location.md`.

---

## INTAKE → INVESTIGATE

### Entry Criteria
- [ ] Task type determined (new_pr, review_response, issue_fix, ci_fix, local_task, candidate_discovery)
- [ ] Repo identity confirmed (name, remotes, current branch)
- [ ] Intelligence mode detected (full_gitnexus / degraded_gh / degraded_local)
- [ ] `$ARTIFACT_DIR` created outside the repo
- [ ] `$ARTIFACT_DIR/state.json` and `$ARTIFACT_DIR/task.json` written
- [ ] Safety snapshot taken (`$ARTIFACT_DIR/snapshots/preflight.patch`)

### Exit Criteria
- [ ] Task normalized into `task.json` with type, source_url, objective
- [ ] Permissions set: edit/test/commit = true, push/post/force_push = false
- [ ] If review_response: review comments fetched and decomposed

### Blockers (do not advance)
- Repo cannot be identified
- Dirty tree contains unknown user edits
- GitHub context cannot be fetched (for review/PR modes)

---

## INVESTIGATE → PLAN

### Entry Criteria
- [ ] Repo intelligence gathered (files, tests, CI, conventions)
- [ ] Related files and tests identified
- [ ] Prior related PRs/issues checked (GitNexus or gh)

### Exit Criteria
- [ ] `$ARTIFACT_DIR/repo_intelligence.md` written
- [ ] For review mode: `$ARTIFACT_DIR/review_decomposition.md` written with task queue
- [ ] Risk areas identified

### Blockers (do not advance)
- No relevant files found
- No test path exists and no source-level proof possible

---

## PLAN → IMPLEMENT

### Entry Criteria
- [ ] `$ARTIFACT_DIR/contract.md` exists with: objective, required outcomes, allowed/forbidden changes, validation plan
- [ ] `$ARTIFACT_DIR/patch_plan.md` written with per-file edit plan
- [ ] Allowed files list is specific (not "the whole repo")
- [ ] Validation plan includes actual commands

### Exit Criteria
- [ ] Contract and patch plan written
- [ ] Scope boundaries clear
- [ ] **`$ARTIFACT_DIR/dod.md` generated** with issue-specific, concrete, verifiable items (not generic template text)
  - Each item names a specific file, function, test command, or observable behavior
  - Test items include the exact command to run and expected pass count
  - All review items (R1, R2, ...) are listed if in review_response mode

### Blockers (do not advance)
- No minimal change path identified
- No validation path exists
- Contract is too broad
- `dod.md` not generated or contains placeholder text

---

## IMPLEMENT → VALIDATE

### Entry Criteria
- [ ] Code changes complete
- [ ] All changed files within contract scope
- [ ] No unrelated changes (formatting churn, dependency additions, scope creep)
- [ ] Each changed file has clear explanation

### Exit Criteria
- [ ] Diff reviewed against contract scope
- [ ] No files outside allowed list touched
- [ ] Code follows existing style/patterns
- [ ] **Plan compliance check run** — `patch_plan.md` planned files vs `git diff --name-only` compared
  - Missing-from-plan: planned files not touched → go back and complete them
  - Extra-not-in-plan: touched files not in plan → classify and handle per SKILL.md rules
  - `state.plan_compliance` recorded with `compliant` boolean
- [ ] Every changed non-test source file has a corresponding test file touched or justified
- [ ] **Commit hygiene: NO Co-authored-by, AI bylines, WIP, debug, or temp commits in branch history**
  - `git log --format="%s%n%b"` must be clean
  - Violations must be fixed via `git rebase -i` before advancing

### Blockers (do not advance)
- Files outside contract modified
- Dependency added without approval
- Formatting-only changes mixed with logic changes
- Planned files not touched (unless contract updated to remove them)
- Untested source changes with no documented justification
- **Review items not addressed** (review_response mode): any required_change or blocker item in review_decomposition.md not marked complete
- **Files mentioned in review items not modified**: git diff does not show changes to files referenced by reviewer comments
- **Commit hygiene violations**: Co-authored-by trailers, AI bylines, WIP/debug/temp commits in branch

---

## VALIDATE → SELF_REVIEW

### Entry Criteria
- [ ] All validation commands from the contract's validation plan were run
- [ ] `$ARTIFACT_DIR/validation_ledger.md` written with honest results
- [ ] No fake validation

### Exit Criteria
- [ ] Validation ledger is honest and complete
- [ ] All critical tests pass
- [ ] Any failures are explained

### Blockers (do not advance)
- Validation commands were not actually run
- Validation ledger contains fabricated results
- Critical tests fail without explanation

---

## SELF_REVIEW → PACKAGE

### Entry Criteria
- [ ] Hostile review completed using `references/hostile-review-checklist.md`
- [ ] `$ARTIFACT_DIR/hostile_review.md` written
- [ ] All "no" or "unclear" answers addressed

### Exit Criteria
- [ ] Hostile review verdict is PASS
- [ ] Edge cases handled or documented
- [ ] **Hostile review covers all required review items** (if review_decomposition.md exists):
  - Each required_change/blocker item has a corresponding finding in hostile_review.md
  - No generic "PASS — all good" without per-item coverage

### Blockers (do not advance)
- Hostile review found unresolved correctness issues
- Alternate code paths might be broken
- Tests missing for core fix

---

## PACKAGE → APPROVAL

### Entry Criteria
- [ ] `$ARTIFACT_DIR/pr_body.md` written (for new PRs)
- [ ] `$ARTIFACT_DIR/review_response.md` written (for review responses)
- [ ] PR body only includes validation commands that were actually run
- [ ] `$ARTIFACT_DIR/approval.md` written using the approval template
- [ ] Every item in `$ARTIFACT_DIR/dod.md` is either checked or has a documented exception

### Exit Criteria
- [ ] Approval artifact is complete and scannable
- [ ] Preflight check passes
- [ ] Branch tracks correct remote (fork, not upstream)
- [ ] `dod.md` status table populated in `approval.md` — no unchecked items without exception

### Blockers (do not advance)
- Preflight check fails
- PR body contains un-run validation claims
- Branch tracks wrong remote
- `dod.md` has unchecked implementation or test items

---

## APPROVAL → POSTMORTEM

### Entry Criteria
- [ ] User explicitly approved the action
- [ ] Approved action matches what's in `approval.md`

### Exit Criteria
- [ ] Action executed exactly as approved
- [ ] terminal outcome recorded and `state.json` can advance to POSTMORTEM
- [ ] User confirmed what was done

---

## Emergency Override

If the user explicitly wants to skip phases:

1. Record the override in `$ARTIFACT_DIR/override.md`:
   ```markdown
   ## Override
   - Phases skipped: [list]
   - Reason: [user's reason]
   - Timestamp: [ISO date]
   - User confirmed: [what they said]
   ```
2. Move to `BLOCKED` with `return_to_phase_after_recovery` set to the current phase.
3. Present the override artifact and the exact gates that would be bypassed.
4. Continue only for local/experimental work after explicit confirmation. Do not set `state.json` directly to the target phase for upstream PR work; the phase-boundary hook must still enforce valid transitions.

This exists for local/experimental work. Never use for upstream PRs.
