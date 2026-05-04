# Phase 4: IMPLEMENT

Read this file at the START of IMPLEMENT before doing any work.

---

Execute the patch plan. Apply the smallest correct change. **Every changed source
file MUST have corresponding test changes.** No exceptions without documented justification.

## Coding Discipline

Companion plugins are optional inputs. PRForge policy gates are mandatory outputs.

1. If `andrej-karpathy-skills` is installed:
   - PRForge MUST treat its coding-discipline rules as mandatory during IMPLEMENT.
   - Every edit MUST comply with: think before coding, prefer simplicity, surgical changes, goal-driven execution.
   - Failure to comply BLOCKS phase exit or REDIRECTS to PLAN to update contract/patch_plan/dod.

2. If `andrej-karpathy-skills` is not installed:
   - PRForge MUST enforce the same discipline via contract.md, patch_plan.md, and hooks.
   - Absence of the external plugin MUST NOT weaken IMPLEMENT enforcement.

3. IMPLEMENT cannot complete unless changed files comply with the discipline contract.

## Rules

- Only touch files listed in the contract
- No unrelated changes, no formatting churn, no dependency additions
- Follow existing code style and test patterns
- After each file edit, update `state.json` `changed_files`
- If you discover a secondary issue: fix it if it's small and clearly broken (bug, null check, typo).
  Commit it separately, label it in the PR body under "## Additional Fix", update contract/dod.md.
  If it's large or requires its own investigation, note it in `hostile_review.md` and leave it alone.
- **Never use `--trailer "Co-authored-by: ..."` or any co-author attribution on commits.**
  Git commit author is the configured human git identity (`git config user.name`/`user.email`).
  `gh api user` is for GitHub ownership checks only — do not use it to set commit authorship.

## Test Requirement (HARD)

For every non-test source file you change, ONE of the following MUST be true:
1. A corresponding test file already exists and you update it to cover the change
2. A corresponding test file already exists and the existing tests still pass (no behavioral change)
3. You create a new test file for the changed source file
4. The change is purely additive (new function/file) and you add tests for it

**Before leaving IMPLEMENT, run a quick test check:**
```bash
# Verify every changed source file has a test sibling
for f in $(git diff --name-only | grep -vE '\.(test|spec)\.'); do
  base=$(basename "$f" | sed 's/\.[^.]*$//')
  dir=$(dirname "$f")
  found=$(find "$dir" -maxdepth 2 \( -name "${base}.test.*" -o -name "${base}.spec.*" \) 2>/dev/null | head -1)
  if [ -z "$found" ]; then
    echo "MISSING TEST: $f"
  fi
done
```
If any file says "MISSING TEST":
- **Default action: create the test.** Add a test file following the repo's existing test patterns.
  Do not ask permission — adding a test for your own change is expected.
- Only escalate to user if: the file is infrastructure/config with no testable logic, the test
  framework is unavailable locally, or creating the test would require production data/secrets.
  In those cases, document the justification and continue.
Do not proceed to VALIDATE with untested source changes and no justification.

## Plan Compliance Check — run before leaving IMPLEMENT

```python
# Use python3 for reliable extraction — avoids sed/grep fragility on special chars
import re, subprocess

plan = open('.prforge/patch_plan.md').read()
# Matches both: ## File 1: `path/to/file.ts`  and  ## File 1: path/to/file.ts
planned = re.findall(r'^## File \d+:\s+`?([^`\n]+)`?', plan, re.MULTILINE)
planned = [p.strip() for p in planned if p.strip()]

actual = subprocess.check_output(['git', 'diff', '--name-only'], text=True).splitlines()
actual = [f for f in actual if f]

missing_from_plan = [f for f in planned if f not in actual]
extra_not_in_plan = [f for f in actual if f not in planned]

if missing_from_plan:
    print("MISSING FROM PLAN (planned but not touched):")
    for f in missing_from_plan: print(f"  - {f}")
if extra_not_in_plan:
    print("EXTRA NOT IN PLAN (touched but not in plan):")
    for f in extra_not_in_plan: print(f"  - {f}")
if not missing_from_plan and not extra_not_in_plan:
    print("Plan compliance: OK")
```

If `MISSING_FROM_PLAN` is non-empty: **finish the planned work** — go back and make the
remaining edits before advancing. Do not skip planned files silently. Only remove a planned
file from scope if investigation shows it's genuinely unnecessary; update `contract.md` and
`dod.md` to reflect the change before continuing.

If `EXTRA_NOT_IN_PLAN` is non-empty, classify each extra change:

| Kind | Action |
|------|--------|
| Test files / type files required by planned edits | Keep. Update contract to list them. |
| Clearly broken code found while working (bug, typo, bad null check) | Keep IF it's small and isolated. Add to contract as a separate labeled section: `## Additional Fix: [description]`. Commit it separately with its own commit message. Surface clearly in PR body. |
| Refactor, cleanup, "while I'm here" improvements | **Remove.** Not now. Note it in `hostile_review.md` as a follow-up suggestion. |
| Unrelated feature or scope expansion | **Remove.** Hard stop — never ship scope creep. |

**Additional fixes must be:**
- Small (ideally 1-5 lines)
- Clearly described in the PR body under a separate "## Additional Fix" section
- Committed in a separate commit from the main change so the maintainer can review them independently
- Not touching anything that requires its own test suite or investigation

If an additional fix is too large to be "clearly separated," it belongs in a separate PR. Note it in `hostile_review.md` and move on.

Record compliance result in `state.json` under `plan_compliance`.

## For Review Response Mode

- Address each required item from the review decomposition — ALL of them, no exceptions
- Remove any changes the maintainer flagged as out of scope
- Add tests for any edge cases the maintainer called out
- Before leaving IMPLEMENT, verify every required item in the task queue is marked complete

## Update state after each major action

```json
{
  "phase": "IMPLEMENT",
  "completed_tasks": ["R1", "R2"],
  "changed_files": ["packages/core/src/config/foo.ts", "packages/core/test/config/foo.test.ts"]
}
```

---

## PHASE EXIT GATE — IMPLEMENT

Before advancing to VALIDATE, all of the following must be true:

- [ ] All planned files in `patch_plan.md` have been touched (or contract updated with reason)
- [ ] No unexpected files in `git diff` (or classified and handled per table above)
- [ ] Every changed source file has a corresponding test file or documented justification
- [ ] Plan compliance result recorded in `state.json` under `plan_compliance`
- [ ] For `review_response` mode: every required item in task queue marked complete
- [ ] `state.json` `changed_files` updated
- [ ] `state.json` phase updated to `IMPLEMENT`
