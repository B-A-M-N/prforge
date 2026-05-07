---
name: pr
description: "PRForge — delegated PR execution harness. Handles any PR, issue, review link, or contribution task end-to-end. Only stops for approval before publishing."
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, WebFetch, Agent, Task
---

<!-- 
PRForge Gate-Scoped Autonomy Model:
- This command declares ALL tools the agent may need across all phases
- The phase-boundary hook (phase-boundary.sh) enforces per-phase tool restrictions
- Allowed actions pass silently; out-of-envelope actions get redirected, not killed
- Gate transitions require checkpoint satisfaction, not per-action approval
-->

# /pr — PRForge Entry Point

You are activating PRForge. Follow the SKILL.md workflow at `skills/prforge/SKILL.md`.

## Input

The user has provided: {{ARGS}}

## What to do

1. **Determine the task type** from the input:
   - GitHub issue link → `new_pr`
   - GitHub PR link (own) → `pr_polish`
   - Review link / pasted comments → `review_response`
   - Failing CI / "fix CI" → `ci_fix`
   - "find PR candidates" / "find issues" → `candidate_discovery`
   - Local task description → `local_task`

2. **Check for existing state:** Resolve `$ARTIFACT_DIR` from repo `.prforge-run`, then read `$ARTIFACT_DIR/state.json`.
   - If found, read it and resume from the current phase.
   - If not found, start from INTAKE and create outside-repo artifacts plus a `.prforge-run` pointer.

3. **At INTAKE, run memory preflight** before any investigation:
   ```bash
   python3 $PRFORGE_HOME/scripts/preflight_injector.py inject \
     --repo <org/repo> \
     --files "<changed_files>" \
     --issue-type <bug|feature|docs|test|refactor>
   ```
   Inject relevant prior lessons into INTAKE context. If prior lessons exist, present them as:
   ```
   Relevant prior lessons:
   - [repo-scoped: org/repo, subsystem X]: <lesson>
     Evidence: PR #123 review comment
   ```

4. **Execute the full pipeline** from the current phase through to COMPLETE:
   - INTAKE → INVESTIGATE → PLAN → IMPLEMENT → VALIDATE → SELF_REVIEW → PACKAGE → APPROVAL
   - After APPROVAL (user approves and action executes): POSTMORTEM → MEMORY_INDEX → COMPLETE
   - Do NOT ask the user to drive each phase. Run them automatically.
   - Show brief progress notes as you work.

5. **At APPROVAL:** Write `$ARTIFACT_DIR/approval.md` and present it to the user.
   - Keep it scannable — the user should understand it in 15 seconds.
   - Wait for explicit approval before any upstream-facing action.

6. **After approval:** Execute exactly what was approved. Then continue:
   - Set `outcome` in state.json (MERGED/CLOSED/ABANDONED/REVERTED) based on result
   - Advance to POSTMORTEM phase
   - Run postmortem analysis
   - Index lessons into memory
   - Confirm to the user what was learned

## Key rules

- You may edit code, run tests, and prepare artifacts without asking.
- You may NOT push, create PR, post comments, or force-push without user approval.
- If you hit a blocker, present it clearly with a suggested fix.
- Never claim tests passed unless actually run.
- Keep progress notes brief. Put details in `$ARTIFACT_DIR` artifacts outside the repo.
- Memory phases (POSTMORTEM, MEMORY_INDEX) run automatically after APPROVAL — do not ask the user to trigger them.
