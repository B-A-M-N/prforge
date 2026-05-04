# Coding Discipline Policy (PRForge Built-in Fallback)

This is the mandatory coding-discipline contract used when `andrej-karpathy-skills`
plugin is not installed. PRForge MUST enforce these rules during every PR run.

## Core Rules (MANDATORY)

### 1. Think Before Coding
State assumptions and success criteria before making any edit.
- What is the minimal change that solves the issue?
- Which files are allowed by the contract?
- What tempting cleanup must be avoided?
- What test proves the change?

### 2. Prefer Simplicity First
The smallest correct fix is always better than architectural cleanup.
- No new abstractions unless the issue cannot be solved without them.
- No rename, reformat, reorganize, or modernize unrelated code.
- No broad rewrites unless explicitly contract-approved.

### 3. Make Surgical Changes
Every edited line must map to one of:
- Issue requirement
- Reviewer requirement
- Test requirement
- Contract-approved incidental fix

### 4. Goal-Driven Execution
Complete the stated objective. Do not:
- Refactor for style
- Update dependencies without approval
- Reorganize project structure
- Add "while I'm here" improvements

## Enforcement

- PLAN cannot complete unless `coding_discipline.md` exists and is satisfied.
- IMPLEMENT cannot complete unless changed files comply with the discipline contract.
- SELF_REVIEW cannot complete unless the discipline audit passes.
- PACKAGE cannot produce `approval.md` unless the discipline verdict is PASS or WARNING with justification.
- APPROVAL cannot proceed if discipline status is BLOCKED.

## Recovery Paths

| Violation | Recovery |
|------------|-----------|
| Files outside contract touched | Revert unexpected files OR update contract.md, patch_plan.md, dod.md → return to PLAN |
| Over-engineering detected | Revert unnecessary changes → return to IMPLEMENT |
| Scope creep | Revert extra changes OR regenerate contract → return to PLAN |
| Missing tests for changes | Create tests → return to IMPLEMENT |
