# Policy: Scope Control

Read this file at activation. Always active — applies to every mode.

---

## Rule 3: Never claim tests passed unless actually run

Maintain the validation ledger honestly.

Every entry in `validation_ledger.md` must be one of:
- `Ran and passed: <command> — N tests passed` (only when actually run and passed)
- `Ran and failed: <command> — <reason>` (honest failure record)
- `Not run: <command> — <reason>` (with explicit reason)

**Never write:**
- "should pass"
- "appears to work"
- "likely passing"
- "tests should be green"

If a test command was not run, say so. Never imply it passed when it wasn't run.

## Rule 4: Never broaden scope without a contract update

If you discover a secondary issue while implementing:
- **Small, clearly broken** (bug, typo, bad null check): fix it IF isolated, commit separately,
  label in PR body as "## Additional Fix", update contract and dod.md to reflect it.
- **Large or requires investigation**: note it in `hostile_review.md` as a follow-up suggestion.
  Do NOT touch it in this PR.

**Never ship scope creep.** Unrelated features, refactors, or cleanup belong in separate PRs.

If an additional fix is too large to be "clearly separated," it belongs in a separate PR.
Note it in `hostile_review.md` and move on.

## Contract Enforcement Rules

The `contract.md` defines the scope boundary for the entire run. The agent must enforce it:

**Allowed changes** — only files explicitly listed in `contract.md` may be modified.
Any modification outside the allowed list is a scope violation.

**Forbidden changes** — the contract explicitly lists what may NOT be touched.
Common forbidden categories:
- Dependency updates
- Formatting-only churn
- Public API changes not required by the task
- Touching unrelated providers/modules
- Rewriting config architecture

**If a file needs to be added to the contract** (e.g. a test file that's clearly required by
a planned edit): update `contract.md` and document why. Do not silently expand scope.

**Scope delta check** (Guard #7, run at PACKAGE):

```bash
git diff --name-only upstream/main...HEAD
```

Cross-reference with contract allowed files. Any file not in the contract that appears
in the diff must be classified:
- Clearly necessary (test file, type file): add to contract, document it
- Scope creep: remove before packaging

Record in `state.scope.delta_check.scope_clean`. If `false`, block approval.

## Blast Radius Monitoring

The blast-radius hook automatically computes after every Write/Edit:
- Files changed vs contract allowed files
- Unexpected files (scope creep detection)
- Test coverage ratio
- Dependency depth (files importing changed files)
- Public API surface touched
- Overall score: `low` / `medium` / `high`

Check `state.blast_radius.score` during SELF_REVIEW. Score `high` triggers
`READY_WITH_WARNINGS` at minimum.
