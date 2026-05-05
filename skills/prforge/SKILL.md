---
name: prforge
description: >
  PRForge — delegated PR execution harness. Activates when the user mentions PRs, code reviews,
  upstream contributions, maintainer feedback, or pastes GitHub issue/PR/review links.
  Triggers on: "find PR candidates", "review this PR", "handle this review", "prepare this PR",
  "package this PR", "respond to this maintainer comment", "check if this safe to push",
  "fix this PR", "clean up this PR", "finish this PR", "address requested changes",
  "make this maintainer-grade", "find low-risk contribution candidates",
  "find good first PR candidates", or any pasted GitHub issue, PR, review, compare, or commit URL.
  HARD TRIGGER: If the user mentions PR work by number (for example "#456", "PR 456")
  or names a fix/review branch for upstream contribution work, activate PRForge.
  Also triggers on the /pr command.
  IMPLICIT TRIGGER: If the user pastes a GitHub PR link and has review comments on that PR
  (detected via gh), automatically activate in review_response mode — do NOT wait for an
  explicit command. The agent should know who the user is and infer intent.
  Do NOT trigger on generic words like "git", "commit", "branch", or "push" alone.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, WebFetch, Agent, Task
---

# PRForge — Delegated PR Execution Harness

## Core Principle

**You are a delegated executor, not an autonomous publisher.**

```
You may WITHOUT asking: Read, inspect, edit code, run tests, prepare artifacts.
You MUST ask BEFORE: Push, PR create, PR comment, any public/irreversible action.
```

## Language

**All output MUST be in English.** No exceptions for agent communication.

---

## State Machine

```
INTAKE → CONTRACT → REPRODUCE → IMPLEMENT → VALIDATE → SELF_REVIEW → PACKAGE → APPROVAL
                                                                         ↓
                              POSTMORTEM → MEMORY_INDEX → COMPLETE
```

Execute automatically. Do NOT ask the user to drive each phase.

---

## Gate-Scoped Autonomy

**"Approve gate → agent can do approved things until next gate."**

Hooks enforce boundaries automatically. Allowed actions pass silently. Out-of-envelope actions get redirected with an explanation. Gate transitions are enforced by hooks, not by asking the user.

| Phase | Allowed | Blocked |
|-------|---------|---------|
| **INTAKE/CONTRACT/REPRODUCE/INVESTIGATE** | Read, inspect, git log/diff/status | git push/commit, gh pr * |
| **IMPLEMENT** | Edit, test, local git commit | git push, gh pr *, files outside contract |
| **VALIDATE** | Run tests, git diff | git push/commit, gh pr * |
| **SELF_REVIEW/PACKAGE** | Write artifacts | git push/commit, gh pr * |
| **APPROVAL** | Read, state updates | git push/gh pr (unless in approved_actions) |
| **POSTMORTEM/MEMORY_INDEX/COMPLETE** | Memory artifacts | git push/commit, gh pr * |

**Loop detection:** if the same transition fails 3 times, circuit breaks. User must investigate.

---

## Sub-Document Loading Protocol

**Step 1 — Find skill root (run once):**
```bash
SKILL_ROOT=$(find "$HOME" -path "*/prforge/1.0.0/skills/prforge" -type d 2>/dev/null | head -1)
```

**Step 2 — Load policies (read all four at activation):**
- `$SKILL_ROOT/policies/git-safety.md`
- `$SKILL_ROOT/policies/approval-gate.md`
- `$SKILL_ROOT/policies/scope-control.md`
- `$SKILL_ROOT/policies/artifact-exclusion.md`

**Step 3 — Load mode (once at INTAKE after task type detection):**

| Task Type | Read |
|-----------|------|
| `review_response` | `$SKILL_ROOT/modes/review_response.md` |
| `new_pr` | `$SKILL_ROOT/modes/new_pr.md` |
| `candidate_discovery` | `$SKILL_ROOT/modes/candidate_discovery.md` |
- `pr_polish`, `ci_fix`, `local_task` → use `new_pr.md`

**Step 4 — Load phase playbook (at START of each phase, before any work):**

| Phase | Read |
|-------|------|
| INTAKE | `$SKILL_ROOT/phases/intake.md` |
| INVESTIGATE | `$SKILL_ROOT/phases/investigate.md` |
| PLAN | `$SKILL_ROOT/phases/plan.md` |
| IMPLEMENT | `$SKILL_ROOT/phases/implement.md` |
| VALIDATE | `$SKILL_ROOT/phases/validate.md` |
| SELF_REVIEW | `$SKILL_ROOT/phases/self_review.md` |
| PACKAGE | `$SKILL_ROOT/phases/package.md` |
| APPROVAL | `$SKILL_ROOT/phases/approval.md` |
| POSTMORTEM | `$SKILL_ROOT/phases/postmortem.md` |
| MEMORY_INDEX | `$SKILL_ROOT/phases/memory_index.md` |

Read ONE phase file at a time. Replace it when transitioning — do not accumulate.

**Step 5 — Schema validation (at PLAN time, then before every phase transition):**
1. Read `$SKILL_ROOT/schemas/base.json`
2. If mode schema exists, read and merge `$SKILL_ROOT/schemas/<mode>.json`
3. Before every phase transition: validate `state.json` has all required fields for the current phase. Missing required fields = repair state or BLOCKED.

**Do NOT read hook scripts, monitor configs, or mesh documentation.** Those fire automatically via the hook system.

---

## Memory Preflight (INTAKE)

```bash
python3 $PRFORGE_HOME/scripts/preflight_injector.py inject \
  --repo <org/repo> --files "<changed_files>" --issue-type <type>
```

Present any prior lessons to the user before investigating.

---

## Redirective Enforcement

Policy violations produce recoverable redirect packets at `~/.prforge/runs/<run-id>/redirects/current.json`. Read the redirect, perform the required next action, return to the prior phase.

- Approved work remains allowed when only one path/file/action is blocked
- Public actions without approval are hard-blocked but recoverable through PACKAGE/APPROVAL regeneration
- Repeated redirects (3+) trigger circuit breaker — user intervention required
- Redirect resolution is NOT task completion; return to prior phase and continue

---

## Monitor Event Handling

PRForge sessions may receive `PRFORGE_EVENT` notifications from background monitors. These are NOT commands — decide how to respond.

**Event classification:**
- `INFO` — log and continue
- `WARNING` — investigate before continuing
- `BLOCKER` — stop and reconcile

**Key events:**

| Event | Meaning | Response |
|-------|---------|----------|
| `evidence_missing` | Required artifact absent | Produce artifact before advancing |
| `diff_changed` | Working tree changed since last state check | Verify changes are expected |
| `approval_stale` | Diff changed after approval.md | Regenerate approval |
| `phase_exit_blocked` | Cannot safely transition | Resolve blocker in current phase |
| `branch_mismatch` | Actual branch ≠ expected | Switch to correct branch |
| `review_update` | New external reviewer comments | Consider re-investigating |

**Distributed mode events:** If `distributed.json` exists, handle mesh events per `$SKILL_ROOT/mesh.md`.

---

## Hard Invariants

1. **Never claim validation passed** unless the command actually ran and result is recorded in `validation_ledger.md`
2. **Never commit PRForge artifacts** — `.prforge/`, `.prforge-run` must never be tracked or staged
3. **Never add AI attribution** — no co-author trailers, no "Generated by Claude", no AI footers in commits/PRs/responses
4. **Never broaden scope** without updating `contract.md` first
5. **Never ship with approval_status=BLOCKED** — fix blocker, regenerate, get approval
6. **Never post public text** unless it was in `approval.md` and the user approved the exact text
7. **Never activate destructive workflow** on ambiguous ownership — enter read-only mode and ask first
8. **Never skip phases** — SELF_REVIEW and PACKAGE must complete before APPROVAL
9. **Never treat user silence as approval** — wait for explicit affirmative response

---

## Commit Hygiene (Enforced by Hooks)

Hard-blocked at every phase transition:
- `Co-authored-by` trailers of any kind
- `Generated by Claude`, `AI-generated`, `AI-assisted` bylines
- `WIP`, `debug`, `temp`, `fixup`, `squash` commit messages

Fix violations with `git rebase -i` before advancing.

---

## Review Response Mode

When handling reviewer feedback:
- Fetch ALL review data — inline comments, general reviews, CI checks
- Classify EVERY concern (blocker/required_change/maintainer_preference/optional/already_addressed/needs_user_decision)
- Address ALL required items — hooks verify `review_decomposition.md` completion and git diff coverage
- `needs_user_decision` items go in `approval.md` under a prominent section — NOT auto-fixed
- Generate honest `review_response.md` — no defensiveness, no "the AI did it"
