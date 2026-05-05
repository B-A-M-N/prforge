---
name: pr
description: "PRForge — delegated PR execution harness. Handles any PR, issue, review link, or contribution task end-to-end. Only stops for approval before publishing."
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, WebFetch, Agent, Task
---

# /pr — PRForge Entry Point

You are activating PRForge. Follow the SKILL.md workflow at `skills/prforge/SKILL.md`.

## Input

The user has provided: {{ARGS}}

## Gate-Scoped Autonomy

PRForge uses a **gate-scoped autonomy model**: once a gate is approved, the agent works freely within that phase's envelope. Hooks enforce boundaries — the agent does not ask permission for every action.

**How it works:**
1. You declare expected tools in frontmatter (already done: `allowed-tools: ...`)
2. You write a checkpoint (state.json) defining the current phase
3. Hooks (`phase-gate-enforcer.sh`) automatically block out-of-envelope Bash commands
4. Agent operates autonomously within the gate — no per-action user prompts
5. Gate transitions require checkpoint satisfaction

## What to do

1. **Determine the task type** from the input:
   - GitHub issue link → `new_pr`
   - GitHub PR link (own) → `pr_polish`
   - Review link / pasted comments → `review_response`
   - Failing CI / "fix CI" → `ci_fix`
   - "find PR candidates" / "find issues" → `candidate_discovery`
   - Local task description → `local_task`

2. **Check for existing state:** Look for `.prforge/state.json` in the target repo.
   - If found, read it and resume from the current phase.
   - If not found, start from INTAKE.

3. **At INTAKE, run memory preflight** before any investigation:
   ```bash
   python3 scripts/preflight_injector.py inject \
     --repo <org/repo> \
     --files "<changed_files>" \
     --issue-type <bug|feature|docs|test|refactor>
   ```
   Present any prior lessons to the user.

4. **Execute the full pipeline** using a **Task subagent** for autonomous work:
   - Spawn a subagent via `Task` tool with the full PRForge context
   - The subagent executes: INTAKE → CONTRACT → REPRODUCE → IMPLEMENT → VALIDATE → SELF_REVIEW → PACKAGE → APPROVAL
   - Hooks automatically enforce per-phase boundaries — the subagent works freely within each gate
   - Only stop for user approval at the APPROVAL gate
   - After approval: POSTMORTEM → MEMORY_INDEX → COMPLETE (automatic)

5. **At APPROVAL:** Write `.prforge/approval.md` and present it to the user.
   - Keep it scannable — the user should understand it in 15 seconds.
   - Wait for explicit approval before any upstream-facing action.

6. **After approval:** Execute approved action. Set outcome. Continue to POSTMORTEM → MEMORY_INDEX → COMPLETE.

## Key rules

- Use **Task subagent** for the main pipeline execution — this is what enables gate-scoped autonomy
- The subagent may edit code, run tests, and prepare artifacts without asking
- Hooks block out-of-envelope actions automatically — the subagent gets a redirect message, not a user prompt
- You may NOT push, create PR, post comments, or force-push without user approval at the APPROVAL gate
- If the subagent hits a blocker, present it clearly with a suggested fix
- Never claim tests passed unless actually run
- Memory phases (POSTMORTEM, MEMORY_INDEX) run automatically after APPROVAL
