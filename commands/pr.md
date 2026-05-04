---
name: pr
description: "PRForge — delegated PR execution harness. Handles any PR, issue, review link, or contribution task end-to-end. Only stops for approval before publishing."
---

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

2. **Check for existing state:** Look for `.prforge/state.json` in the target repo.
   - If found, read it and resume from the current phase.
   - If not found, start from INTAKE.

3. **Execute the full pipeline** from the current phase through to APPROVAL:
   - INTAKE → INVESTIGATE → PLAN → IMPLEMENT → VALIDATE → SELF_REVIEW → PACKAGE → APPROVAL
   - Do NOT ask the user to drive each phase. Run them automatically.
   - Show brief progress notes as you work.

4. **At APPROVAL:** Write `.prforge/approval.md` and present it to the user.
   - Keep it scannable — the user should understand it in 15 seconds.
   - Wait for explicit approval before any upstream-facing action.

5. **After approval:** Execute exactly what was approved. Confirm what was done.

## Key rules

- You may edit code, run tests, and prepare artifacts without asking.
- You may NOT push, create PR, post comments, or force-push without user approval.
- If you hit a blocker, present it clearly with a suggested fix.
- Never claim tests passed unless actually run.
- Keep progress notes brief. Put details in `.prforge/` artifacts.
