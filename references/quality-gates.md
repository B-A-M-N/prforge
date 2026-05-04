# PRForge Quality Gates

Each phase has mandatory entry criteria (must be true to enter) and exit criteria
(must be complete before advancing). The agent may NOT skip phases unless the user
explicitly invokes an emergency override.

All artifacts are stored in `.prforge/` (not `.pr-harness/`).

---

## INTAKE → INVESTIGATE

### Entry Criteria
- [ ] Task type determined (new_pr, review_response, issue_fix, ci_fix, local_task, candidate_discovery)
- [ ] Repo identity confirmed (name, remotes, current branch)
- [ ] Intelligence mode detected (full_gitnexus / degraded_gh / degraded_local)
- [ ] `.prforge/` directory created
- [ ] `.prforge/state.json` and `.prforge/task.json` written
- [ ] Safety snapshot taken (`.prforge/snapshots/preflight.patch`)

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
- [ ] `.prforge/repo_intelligence.md` written
- [ ] For review mode: `.prforge/review_decomposition.md` written with task queue
- [ ] Risk areas identified

### Blockers (do not advance)
- No relevant files found
- No test path exists and no source-level proof possible

---

## PLAN → IMPLEMENT

### Entry Criteria
- [ ] `.prforge/contract.md` exists with: objective, required outcomes, allowed/forbidden changes, validation plan
- [ ] `.prforge/patch_plan.md` written with per-file edit plan
- [ ] Allowed files list is specific (not "the whole repo")
- [ ] Validation plan includes actual commands

### Exit Criteria
- [ ] Contract and patch plan written
- [ ] Scope boundaries clear
- [ ] **`.prforge/dod.md` generated** with issue-specific, concrete, verifiable items (not generic template text)
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

### Blockers (do not advance)
- Files outside contract modified
- Dependency added without approval
- Formatting-only changes mixed with logic changes
- Planned files not touched (unless contract updated to remove them)
- Untested source changes with no documented justification

---

## VALIDATE → SELF_REVIEW

### Entry Criteria
- [ ] All validation commands from the contract's validation plan were run
- [ ] `.prforge/validation_ledger.md` written with honest results
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
- [ ] `.prforge/hostile_review.md` written
- [ ] All "no" or "unclear" answers addressed

### Exit Criteria
- [ ] Hostile review verdict is PASS
- [ ] Edge cases handled or documented

### Blockers (do not advance)
- Hostile review found unresolved correctness issues
- Alternate code paths might be broken
- Tests missing for core fix

---

## PACKAGE → APPROVAL

### Entry Criteria
- [ ] `.prforge/pr_body.md` written (for new PRs)
- [ ] `.prforge/review_response.md` written (for review responses)
- [ ] PR body only includes validation commands that were actually run
- [ ] `.prforge/approval.md` written using the approval template
- [ ] Every item in `.prforge/dod.md` is either checked or has a documented exception

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

## APPROVAL → SHIPPED

### Entry Criteria
- [ ] User explicitly approved the action
- [ ] Approved action matches what's in `approval.md`

### Exit Criteria
- [ ] Action executed exactly as approved
- [ ] `state.json` phase updated to SHIPPED
- [ ] User confirmed what was done

---

## Emergency Override

If the user explicitly wants to skip phases:

1. Record the override in `.prforge/override.md`:
   ```markdown
   ## Override
   - Phases skipped: [list]
   - Reason: [user's reason]
   - Timestamp: [ISO date]
   - User confirmed: [what they said]
   ```
2. Update `.prforge/state.json` phase to the target phase.
3. Proceed with the remaining gates.

This exists for local/experimental work. Never use for upstream PRs.
