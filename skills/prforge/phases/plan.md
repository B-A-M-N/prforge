# Phase 3: PLAN

Read this file at the START of PLAN before doing any work.

---

## Dynamic Schema Loading

At PLAN time, load and merge the state schema:

1. Read `$SKILL_ROOT/schemas/base.json`
2. Based on `state.task`, read the mode schema:
   - `review_response` → `$SKILL_ROOT/schemas/review_response.json`
   - `new_pr` / `ci_fix` / `local_task` → `$SKILL_ROOT/schemas/new_pr.json`
   - `candidate_discovery` / `pr_polish` → base only
3. Write merged field requirements to `.prforge/state.schema.json`
4. From this point forward, verify every `state.json` write includes all fields required for the current phase per the merged schema.

---

Create the scope contract and patch plan.

## `contract.md`

```markdown
# PR Contract

## Objective
[What this PR accomplishes in one sentence.]

## Required Outcomes
- [ ] [Specific outcome — e.g., "Fix OAuth timeout override path"]
- [ ] [Specific outcome — e.g., "Add regression test"]
- [ ] [Specific outcome — e.g., "Remove unrelated refactor"]

## Allowed Changes
- [Specific file/pattern that may be modified]
- [Specific file/pattern that may be modified]

## Forbidden Changes
- Dependency updates
- Formatting-only churn
- Public API changes not required by the task
- Touching unrelated providers/modules
- Rewriting config architecture

## Validation Plan
- Targeted tests: `[command]`
- Package tests: `[command]`
- Typecheck: `[command]`
- Lint: `[command]`

## Release Gate
PRForge may modify code, run commands, amend commits, and prepare responses.
PRForge may not push, create PR, post comments, or force-push without user approval.
```

## `patch_plan.md`

```markdown
# Patch Plan

## File 1: `path/to/file.ts`
**Reason:** [Why this file needs to change]
**Planned change:** [What specifically will be changed]
**Risk:** [What could go wrong]
**Test:** [What test will verify this]

## File 2: `path/to/test.test.ts`
**Reason:** [Why this test needs to be added/updated]
**Planned change:** [What the test covers]
```

## `dod.md` — Definition of Done (generated at PLAN time, checked at APPROVAL)

After writing the contract and patch plan, generate `.prforge/dod.md`. This is a
**concrete, issue-specific checklist** — not a generic template. Every item must be
verifiable as true or false. A weak model cannot mark this done without it actually
being done.

```markdown
# Definition of Done — [Issue title / PR title]

Generated: [ISO timestamp]
Issue: [URL]
Repo: [owner/repo]
Branch: [branch name]

## Implementation
- [ ] [Specific function/file/behavior changed — e.g. "Fix token refresh in src/auth/oauth.ts:refreshToken()"]
- [ ] [Only files in contract modified — list them]
- [ ] No files outside contract were touched

## Tests (HARD — cannot ship without these)
- [ ] [Specific test file updated/created — e.g. "src/auth/oauth.test.ts covers refreshToken() error path"]
- [ ] Test command run and passed: `[exact command]`
- [ ] Test output confirmed: [N tests passed, 0 failed]

## Validation
- [ ] `[typecheck command]` passed
- [ ] `[lint command]` passed (or pre-existing failures documented as unrelated)
- [ ] No new warnings introduced in changed files

## Scope
- [ ] Diff is ≤ [N] files (from contract)
- [ ] No dependency changes
- [ ] No public API signature changes (or documented and approved)

## PR Quality
- [ ] PR body written and honest (no fake validation claims)
- [ ] Commit message is clean (no AI bylines, no WIP, no co-author trailers)
- [ ] `.prforge/` not staged or tracked

## Review Items (review_response mode only)
- [ ] R1 — [description]: addressed
- [ ] R2 — [description]: addressed
- [ ] All `needs_user_decision` items surfaced in approval.md

## Final Gate
- [ ] `state.release.approval_status` is READY_TO_SHIP or READY_WITH_WARNINGS (not BLOCKED)
- [ ] User has reviewed and approved approval.md
```

**After writing `dod.md`, immediately hash it and record in `state.json`:**
```python
import hashlib, json, datetime

dod_content = open('.prforge/dod.md', 'rb').read()
dod_hash = hashlib.sha256(dod_content).hexdigest()

items_total = dod_content.decode().count('\n- [ ]') + dod_content.decode().count('\n- [x]')

state = json.load(open('.prforge/state.json'))
state.setdefault('dod', {})
state['dod']['generation_hash'] = dod_hash
state['dod']['generated_at'] = datetime.datetime.utcnow().isoformat() + 'Z'
state['dod']['items_total'] = items_total
state['dod']['items_checked'] = 0
state['dod']['evidence_verified'] = False
state['dod']['tampered'] = False
open('.prforge/state.json', 'w').write(json.dumps(state, indent=2))
print(f"DoD hash recorded: {dod_hash[:16]}...")
```

**At APPROVAL, the DoD is verified two ways — both must pass:**

1. **Tamper check** — recompute hash of current `dod.md` and compare to `state.dod.generation_hash`.
   If they differ, the agent edited the checklist. This is a hard block — regenerate DoD from scratch
   and re-run all phases. Editing `dod.md` to self-check is not allowed.

2. **Evidence cross-reference** — for each checked item, require corroborating evidence:
   - Implementation items (`- [x] Fixed X in file.ts`) → `git diff` must show that file was actually changed
   - Test items (`- [x] Test command passed`) → `validation_ledger.md` must have a matching "Passed" entry with that exact command
   - Review items (`- [x] R1 addressed`) → `review_decomposition.md` must show R1 status as `addressed`
   - Scope items (`- [x] No files outside contract`) → `state.scope.delta_check.unexpected_files` must be empty

   If a checked item has no corroborating evidence, it is treated as unchecked.

**Every unchecked (or evidence-missing) item in `dod.md` is a blocker.** The approval artifact
must include the DoD status table. If any item is unchecked or unverifiable, the approval status is BLOCKED.

---

## PHASE EXIT GATE — PLAN

Before advancing to IMPLEMENT, all of the following must be true:

- [ ] `contract.md` written with objective, required outcomes, allowed files, forbidden changes, validation plan
- [ ] `patch_plan.md` written with per-file change plan
- [ ] `dod.md` generated with concrete, issue-specific checklist items
- [ ] DoD hash recorded in `state.dod.generation_hash`
- [ ] `state.schema.json` written with merged base + mode field requirements
- [ ] `state.json` phase updated to `PLAN`
