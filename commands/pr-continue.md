---
name: pr-continue
description: "Resume PRForge after it was blocked or paused."
---

# /pr-continue — Resume PRForge

PRForge was paused (blocked, failed test, or waiting for context). Resume work.

## Step 0 — Check for mesh inbox (distributed mode)

Before reading state.json, check if a distributed job has been assigned:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -f "$REPO_ROOT/.prforge-run" ]; then
  ARTIFACT_DIR=$(awk -F= '$1=="artifact_dir"{print $2}' "$REPO_ROOT/.prforge-run")
else
  ARTIFACT_DIR="${PRFORGE_HOME:-$HOME/.prforge}/runs"
fi
INBOX="$ARTIFACT_DIR/inbox/job.json"
REVISION="$ARTIFACT_DIR/inbox/revision.json"
```

### Step 0a — Check for revision jobs (audit loop)

If `$REVISION` exists, a coordinator has returned this job with required changes after audit:

1. Read `revision.json`. It contains an array of audit findings with `required_changes`.
2. Parse the `audit_result` field: `pass | fail | blocked | needs_more_context`.
3. If `audit_result == "pass"`: the audit passed — remove `revision.json` and continue normally.
4. If `audit_result == "fail"` or `needs_more_context"`:
   - Log: "Audit returned findings — applying required changes before resuming."
   - For each item in `required_changes`:
     - `type == "evidence_missing"`: produce the required artifact before continuing.
     - `type == "state_mismatch"`: reconcile state.json with actual repo state.
     - `type == "scope_violation"`: revert files outside contract scope.
     - `type == "validation_failure"`: re-run tests and update validation_ledger.md.
   - After applying changes: write `outbox/status.json` with `status: "active"` so the
     coordinator picks up the revision for re-audit.
   - Resume from the phase indicated in the revision findings (typically IMPLEMENT or VALIDATE).
5. If `audit_result == "blocked"`: stop and present the blockers to the user.

### Step 0b — Check for new/updated mesh job

If `$INBOX` exists:

1. Read it. Validate that `mesh.enabled == true` and `job.type` is present.
2. Check `state.json` in the run artifact directory if it exists:
   - If `state.job_id == job.job_id` and `state.phase` is not BLOCKED/INTAKE: resume from state.
   - If `state.job_id` differs: warn user — another job may be active. Stop and ask.
   - If state.json absent or phase is INTAKE/BLOCKED: proceed with inbox job.
3. Load mesh supplement: read `$SKILL_ROOT/mesh.md`.
4. Map `job.type` to mode file (see mesh.md for table).
5. Write `state.json` in the run artifact directory with job fields from inbox packet if absent.
6. Write `outbox/status.json` in the run artifact directory with `status: "active"`.
7. Load the SKILL.md routing kernel and proceed from INTAKE phase.

If neither `$INBOX` nor `$REVISION` exists: skip to Step 1 (normal resume).

**Machine 3 guard:** If `distributed.json` exists in the run artifact directory and `role` is `coordinator`
or `auditor`, only `audit_only` job types are permitted. All others: reject with message
"Machine 3 does not execute worker jobs."

## Step 1 — Normal resume (no inbox)

1. Read `state.json` from the run artifact directory to find the current phase and any blocker.
2. Read `blocker.md` or `redirects/current.json` if either exists for details on what paused.
3. Attempt to resolve the blocker:
   - If a test failed: analyze the failure, patch the code, re-run.
   - If context was missing: gather the missing context and continue.
   - If a scope decision was needed: present the decision to the user.
4. Continue the pipeline from the current phase.
5. Run through to APPROVAL or next blocker.

## Key rules

- Do not restart from the beginning. Resume from where you left off.
- If the blocker cannot be resolved automatically, present it to the user clearly.
- Inbox job constraints (`public_actions_require_approval`, etc.) are always enforced
  on top of normal mode rules — they cannot be relaxed.
