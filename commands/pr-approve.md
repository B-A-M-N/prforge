---
name: pr-approve
description: "Approve the current PRForge release gate and execute the pending upstream action. Verifies approval integrity before executing."
---

# /pr-approve — Approve Release Gate (with integrity verification)

The user has approved the release. **Verify integrity BEFORE executing anything.**

## Step 0: Manager Mode check (distributed mode only)

Resolve the current run artifact directory first:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
ARTIFACT_DIR=""
if [ -f "$REPO_ROOT/.prforge-run" ]; then
  ARTIFACT_DIR=$(awk -F= '$1=="artifact_dir"{print $2}' "$REPO_ROOT/.prforge-run")
fi
if [ -z "$ARTIFACT_DIR" ]; then
  ARTIFACT_DIR="$REPO_ROOT/.prforge"  # legacy runs only
fi
```

Check if `$ARTIFACT_DIR/distributed.json` exists. If it does NOT exist, skip to Step 1 (standalone mode unchanged).

If `$ARTIFACT_DIR/distributed.json` exists, read it. If `manager_mode.enabled` is `true`, perform the following **before any other checks**:

### 0a. Required manager artifacts exist
```
$ARTIFACT_DIR/mesh/coordinator_verdict.json  MUST exist
$ARTIFACT_DIR/mesh/auditor_verdict.json     MUST exist
$ARTIFACT_DIR/mesh/manager_verdict.json     MUST exist
$ARTIFACT_DIR/mesh/mesh_certification.json  MUST exist (for low_risk_public authority)
```
If any are missing, stop and tell the user which artifact is missing.

### 0b. Verify signatures

Use `mesh_signing.verify_artifact()` (Python) or manual HMAC-SHA256 to verify the `_signature` field on each artifact:

```python
import json, sys
sys.path.insert(0, "<mesh_scripts_dir>")
from mesh_signing import verify_artifact

for name in ["coordinator_verdict.json", "auditor_verdict.json", "manager_verdict.json"]:
    data = json.load(open(f"{ARTIFACT_DIR}/mesh/{name}"))
    if not verify_artifact(data):
        print(f"SIGNATURE INVALID: {name}")
        sys.exit(1)
```

If any signature is invalid, **stop immediately**. Tell the user: "⚠️ SIGNATURE VERIFICATION FAILED for [artifact]. The artifact may be tampered. Do NOT proceed."

### 0c. Verify manager_verdict decision

Read `$ARTIFACT_DIR/mesh/manager_verdict.json`:
- If `decision` is NOT `manager_certified` or `manager_auto_ship_allowed`, **stop**.
  Tell the user: "Manager Mode blocked: decision=[decision]. Reason: [failure_reason]."
- If `decision` is `manager_certified` (certify_only or internal_actions authority):
  Human `/pr-approve` is still required for any public actions. Continue to Step 1.
- If `decision` is `manager_auto_ship_allowed` (low_risk_public authority):
  Continue to Step 0d.

### 0d. Verify mesh_certification integrity (low_risk_public only)

Read `$ARTIFACT_DIR/mesh/mesh_certification.json`:
1. Verify its signature (same as 0b).
2. Verify current hashes match certified hashes:
   ```python
   import hashlib, json

   cert = json.load(open(f"{ARTIFACT_DIR}/mesh/mesh_certification.json"))
   # Strip _signature for comparison
   cert_data = {k: v for k, v in cert.items() if k != "_signature"}

   # Recompute current diff hash
   import subprocess
   diff_out = subprocess.run(["git", "diff", "--stat"], capture_output=True, text=True).stdout
   staged_out = subprocess.run(["git", "diff", "--cached", "--stat"], capture_output=True, text=True).stdout
   current_diff_hash = hashlib.sha256((diff_out + staged_out).encode()).hexdigest()

   if current_diff_hash != cert["hashes"]["diff"]:
       print("DIFF CHANGED SINCE CERTIFICATION — stale")
       sys.exit(1)
   ```
3. If hashes don't match, **stop**. Tell the user: "⚠️ Code changed since manager certification. Manager verdict is stale."

### 0e. Authority-based action gating

Read `manager_verdict.authority` and enforce:

- **`certify_only`**: May NOT execute any public actions (push, comment, merge, PR create, force_push, delete_branch). Only certification. Human must execute public actions manually.
- **`internal_actions`**: May NOT execute public actions. Same as certify_only for public action gating.
- **`low_risk_public`**: May execute ONLY actions in `allowed_public_actions` list from config. NEVER execute `force_push`, `merge`, `delete_branch` (hard-forbidden). If the requested action is not allowed, stop and tell the user.

If the action is blocked by authority, tell the user: "Manager Mode authority=[authority] does not permit [action]."

If all checks pass, continue to Step 1.

---

## Step 1: Pre-execution integrity checks

Read `$ARTIFACT_DIR/state.json` and verify ALL of the following:

### 1a. Phase check
```
state.phase MUST be "APPROVAL"
```
If not, stop. Tell the user: "PRForge is in phase [phase], not APPROVAL. Run `/pr <task>` first."

### 1b. Required artifacts exist
```
$ARTIFACT_DIR/approval.md          MUST exist
$ARTIFACT_DIR/validation_ledger.md MUST exist
$ARTIFACT_DIR/hostile_review.md    MUST exist
$ARTIFACT_DIR/dod.md               MUST exist
$ARTIFACT_DIR/state.json           MUST exist with approval.approval_id set
```
If any are missing, stop and tell the user which artifact is missing.

### 1c. Approval fingerprint verification

Extract from `state.json`:
- `approval.diff_hash` — SHA256 recorded at approval time
- `approval.validation_hash` — SHA256 recorded at approval time
- `approval.approval_md_hash` — SHA256 recorded at approval time

Now compute current hashes:

```bash
# Diff hash — has the code changed since approval?
CURRENT_DIFF_HASH=$(git diff --stat | sha256sum | awk '{print $1}')

# Staged changes too
CURRENT_DIFF_HASH="$CURRENT_DIFF_HASH$(git diff --cached --stat | sha256sum | awk '{print $1}')"

# Validation ledger hash
CURRENT_VAL_HASH=$(sha256sum "$ARTIFACT_DIR/validation_ledger.md" | awk '{print $1}')

# Approval.md hash
CURRENT_APPROVAL_HASH=$(sha256sum "$ARTIFACT_DIR/approval.md" | awk '{print $1}')

# DoD hash — must match state.dod.generation_hash (not state.approval.dod_hash)
CURRENT_DOD_HASH=$(sha256sum "$ARTIFACT_DIR/dod.md" | awk '{print $1}')
```

**Compare:**

**Tamper Checks:**
- If `CURRENT_DOD_HASH` ≠ `state.dod.generation_hash` → **TAMPERED**. DoD was edited after generation.
  This is not a stale-approval condition — it means the checklist itself was changed.
  Tell the user: "dod.md was modified after it was generated. The DoD must reflect the original plan.
  I need to regenerate the DoD from the contract and re-run all phases."
  Do NOT regenerate approval — the whole run is invalidated. Restart from PLAN. Stop.

**Stale Checks:**
- If `CURRENT_DIFF_HASH` ≠ `approval.diff_hash` → **STALE**. Code changed since approval.
- If `CURRENT_VAL_HASH` ≠ `approval.validation_hash` → **STALE**. Validation changed since approval.
- If `CURRENT_APPROVAL_HASH` ≠ `approval.approval_md_hash` → **STALE**. Approval artifact was edited.

**If any STALE check fails:**
1. Set `state.approval.stale = true`
2. Tell the user:
   ```
   ⚠️ APPROVAL IS STALE

   Code/validation changed after approval.md was generated.
   I need to regenerate the approval artifact before proceeding.

   What changed: [brief description]
   ```
3. Re-run the PACKAGE phase to regenerate `approval.md` with new hashes.
4. Present the new approval. Wait for user confirmation.
5. Only then proceed to Step 2.

### 1d. Evidence cross-reference check

For every checked item in `$ARTIFACT_DIR/dod.md`, verify corroborating evidence:
- Implementation items: `git diff` must show the relevant file changed.
- Test items: `$ARTIFACT_DIR/validation_ledger.md` must have a passing entry for the command.
- Review items: `$ARTIFACT_DIR/review_decomposition.md` must show status addressed.
- Scope items: `state.scope.delta_check.unexpected_files` must be empty.

If any checked item lacks evidence, the approval is invalid. Stop and tell the user: "Evidence missing for DoD item: [item]".

### 1e. Approved actions check

From `state.approval.approved_actions`, verify the action the user is approving is explicitly listed.

If the user says "approve" but `approved_actions` is empty or doesn't contain the action they want, stop and ask for clarification.

### 1e. Dirty tree check (final)

```bash
git status --short
```

If there are uncommitted changes outside PRForge artifact pointers that are NOT part of the approved diff, stop. Tell the user there are unexpected uncommitted changes.

## Step 2: Execute approved action(s)

Execute **only** the actions listed in `state.approval.approved_actions`.

Read the exact command from `state.release.suggested_actions` — do NOT invent new commands.

### Push (normal)
```bash
git push origin <branch>
```

### Force-push
```bash
git push --force-with-lease origin <branch>
```
> Never use raw `--force`. Always `--force-with-lease`.

### Create PR
```bash
gh pr create --title "<title>" --body-file "$ARTIFACT_DIR/pr_body.md"
```

### Post review response
```bash
# Post a general comment on a PR (review response):
gh pr comment <pr_number> --body "<response text>"

# Post a comment on an issue:
gh issue comment <issue_number> --body "<response text>"
```
> `pulls/{number}/comments` creates inline review comments on a specific line — do NOT use it
> for general review responses. Use `gh pr comment` instead.

### Request review
```bash
gh pr request-reviewers <pr_number> --reviewer <username>
```

## Step 3: Post-execution

1. Update `state.json`:
   ```json
   {
     "phase": "POSTMORTEM",
     "outcome": "<MERGED|CLOSED|ABANDONED|REVERTED>",
     "release": { "ready": false },
     "approval": { "stale": false }
   }
   ```
2. Confirm to the user exactly what was done:
   - "Pushed to origin/branch-name"
   - "Created PR: https://github.com/org/repo/pull/N"
   - "Posted review response to PR #N"
3. If the action created a URL (PR, comment), include it in the confirmation.

## Step 4: Memory Continuation

After updating state.json to POSTMORTEM:
1. The POSTMORTEM phase will run automatically (or on next `/pr-continue`)
2. Postmortem analysis generates lessons from the PR cycle
3. MEMORY_INDEX promotes evidence-backed lessons to durable memory
4. COMPLETE finishes the run

The user does NOT need to trigger this — it runs as part of the normal pipeline.

## Safety rules — never violate

- **Never push to `upstream`** — only to `origin` (fork)
- **Never execute an action not in `approved_actions`**
- **Never execute if approval is stale** — always regenerate first
- **Never invent commands** — use exactly what's in `state.release.suggested_actions`
- **Never skip integrity checks** — even if the user says "just do it"
- **Never use `--force`** — always `--force-with-lease`
