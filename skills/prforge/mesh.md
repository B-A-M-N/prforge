# PRForge Mesh — Distributed Mode Supplement

This document supplements SKILL.md when distributed mode is active.
Read this ONLY if `.prforge/distributed.json` exists in the target repo.

## Scaling Models

PRForge supports two distributed scaling models:

**Horizontal Scaling (`/pr-distributed`)** — Multiple machines on the same LAN.
- Watchtower on one machine (coordinator + auditor)
- Workers on other machines via SSH tunnel (editing agents)
- More machines = more capacity
- Each machine has its own worktree root

**Vertical Scaling (`/pr-distributed-local`)** — Multiple Claude instances on ONE machine.
- Watchtower + workers all on same box
- Single machine handles coordination and execution
- No network dependencies, simpler setup

## Worktree Isolation (Both Models)

**Core rule: Never run multiple agents in the same repo checkout.**

Each worker-job gets an isolated worktree:
```
~/.prforge/repos/<repo>.git          # bare mirror (shared, fetch-only)
~/.prforge/worktrees/<repo>/<job_id> # per-job writable worktree
```

The checkout broker (`scripts/mesh/checkout_broker.py`) manages this:
```bash
# Create isolated worktree for a job
python3 scripts/mesh/checkout_broker.py create \
  --repo-url https://github.com/owner/repo.git \
  --repo-key owner/repo \
  --job-id job_9f42a \
  --worker-id worker-a \
  --base-ref origin/main \
  --target-number 3819 \
  --task-slug mcp-client-race

# List active checkouts
python3 scripts/mesh/checkout_broker.py list

# Cleanup after merge/close
python3 scripts/mesh/checkout_broker.py cleanup --job-id job_9f42a

# Quarantine dirty/stale worktree
python3 scripts/mesh/checkout_broker.py quarantine --job-id job_9f42a
```

Branch naming scheme (unique per job):
```
prforge/<target-number>-<task-slug>-<short-job-id>
Example: prforge/3819-mcp-client-race-9f42a
```

Each worktree gets a `.prforge/checkout.json` with metadata for hook verification.

## Lock Model

5 Redis lock types prevent conflicts:

| Lock Type | Key Pattern | Purpose |
|-----------|-------------|---------|
| **Job** | `lease:job:<job_id>` | One worker per job |
| **Target** | `lease:target:<repo>:pr:<number>` | One worker per PR/issue |
| **Branch** | `lease:branch:<repo>:<branch>` | One worker per branch |
| **Path** | `lease:path:<repo>:<file>` | One worker per file (after PLAN) |
| **Public** | `lease:public:<repo>:<branch>` | Serialize push/PR actions |

All locks use `SET key value NX PX <ttl>` with JSON values containing worker_id, job_id, timestamps.

Path locks are acquired **after PLAN** using `scope.json` allowed_paths. All-or-nothing acquisition with rollback.

**Core invariant:** A worker may only mutate source files when it owns the job lease, target lease, branch lease, is inside its assigned worktree, and (after PLAN) owns path leases for the files it edits.

## Stale Worker Handling

```
heartbeat expires → worker marked stale → leases enter grace period
→ dirty worktree quarantined (not deleted)
→ job becomes RECOVERABLE_STALE
→ coordinator decides: recover / requeue / abandon
```

Quarantine path: `~/.prforge/quarantine/<job_id>/`

**Never auto-delete dirty worktrees.** Preserve for recovery.

## Public Action Serialization

Even with parallel workers, public GitHub actions are serialized per PR/branch:
- Only coordinator/manager can execute `git push`, `gh pr create`, `gh pr comment`
- Workers produce artifacts → coordinator/manager certifies and ships
- `lease:public:<repo>:<branch>` ensures single-lane public actions

## Machine-Readable Scope Artifacts

Instead of parsing Markdown, the PLAN phase writes:

```
.prforge/scope.json    -- allowed/forbidden paths, lock status
.prforge/checkout.json -- worktree, branch, job, worker (from broker)
.prforge/locks.json    -- all lease keys held by this job
```

The `mesh-lock-guard.sh` hook reads these to enforce boundaries.

## What distributed mode changes

1. **Job source** — task arrives via `.prforge/inbox/job.json` instead of user command.
2. **Mode auto-detected** — `job.type` determines the mode file to load.
3. **Status reporting** — worker loop reads `.prforge/outbox/status.json` for phase progress.
4. **Constraints enforced** — job packet `constraints` object applies on top of normal mode rules.

## What distributed mode does NOT change

- All phase gates remain non-negotiable.
- SELF_REVIEW → PACKAGE → APPROVAL order still required.
- No push/post/create PR without explicit user approval and /pr-approve.
- .prforge/ artifacts never staged or committed.
- No AI attribution in commits, PR bodies, or review responses.
- All Hard Invariants in SKILL.md still apply.

## .prforge artifact exclusion (ALL artifacts)

The following are NEVER staged or committed (enforced by `.gitignore`, `.git/info/exclude`, and pre-commit hook):

- `.prforge/state.json`, `approval.md`, `dod.md`, `hostile_review.md`, `validation_ledger.md`
- `.prforge/inbox/job.json`
- `.prforge/outbox/status.json`, `submission.json`
- `.prforge/mesh/coordinator_verdict.json`, `auditor_verdict.json`, `manager_verdict.json`, `mesh_certification.json`
- `.prforge/distributed.json`

All are runtime-only artifacts. None belong in git history.

---

## Inbox detection (integrated into /pr-continue)

When `/pr-continue` is invoked, check before anything else:

```bash
INBOX="$(git rev-parse --show-toplevel 2>/dev/null)/.prforge/inbox/job.json"
```

If this file exists:
1. Read and validate against `schemas/mesh_job.json`.
2. Confirm `mesh.enabled == true` in the packet.
3. Map `job.type` to mode file (table below).
4. Write/update `.prforge/state.json` with job fields.
5. Proceed with normal PRForge workflow from INTAKE.

Do not start a new job if `.prforge/state.json` already shows an active non-BLOCKED phase
from a different job_id. Warn the user and stop.

## Job type → mode mapping

| job.type | Mode file to load |
|----------|-------------------|
| `new_pr` | `modes/new_pr.md` |
| `review_response` | `modes/review_response.md` |
| `pr_polish` | `modes/pr_polish.md` |
| `ci_fix_related_to_branch` | `modes/new_pr.md` + CI-fix constraint |
| `audit_only` | `modes/audit_only.md` (Scenario B — proactive scan) |

`audit_only` Scenario A (worker submission review) is triggered by the `worker_submission_ready`
monitor event on coordinator/auditor nodes — not by a job type in the inbox.

## CI-fix constraint (ci_fix_related_to_branch)

When mode is `ci_fix_related_to_branch`, apply these additional constraints
on top of `modes/new_pr.md`:

```
Only fix CI failures that are classified as "related" to the diff.
Do not fix unrelated or "unknown" CI failures.
Do not add new features while fixing CI.
Do not alter test infrastructure beyond making failing tests pass.
Scope is strictly limited to: make related CI checks green.
```

## Status reporting

At each phase transition, write `.prforge/outbox/status.json`:

```json
{
  "job_id": "...",
  "phase": "VALIDATE",
  "status": "active",
  "updated_at": "2026-05-03T15:00:00Z"
}
```

When reaching APPROVAL:

```json
{
  "job_id": "...",
  "phase": "APPROVAL",
  "status": "approval_ready",
  "updated_at": "2026-05-03T15:00:00Z"
}
```

## Constraints enforcement

The `constraints` field in `inbox/job.json` is always active:

| Constraint | Effect |
|-----------|--------|
| `public_actions_require_approval: true` | Enforces approval gate (always true) |
| `only_address_main_review_feedback: true` | Do not address unrelated comments or do unrelated cleanup |
| `ignore_unrelated_ci: true` | Do not fix CI failures classified unrelated |
| `do_not_create_new_pr: true` | This job must push to existing branch, never open a new PR |

---

## Machine 3 read-only constraint

If `.prforge/distributed.json` exists and `distributed.json.roles` contains `"coordinator"` or `"auditor"` (and does NOT contain `"worker"`),
and job type is NOT `audit_only`:

```
REJECT — this node is not a worker.
Do not execute worker jobs.
Log: "Machine 3 attempted to run worker job {job_id}. Blocked."
```

Only `audit_only` mode may run on coordinator/auditor nodes.

---

## Acceptance criteria passthrough

The original acceptance criteria from INTAKE must flow through the entire pipeline
so the auditor can verify real requirements, not just workflow compliance.

### Worker: recording at INTAKE

At INTAKE, read `inbox/job.json` and extract `job.acceptance_criteria`. Write each
item into `state.json` under `task.required_items`:

```json
"task": {
  "type": "new_pr",
  "objective": "Add OAuth2 PKCE support to auth module",
  "required_items": [
    { "id": "req-1", "description": "PKCE flow implemented in auth.py", "kind": "code_change", "status": "pending" },
    { "id": "req-2", "description": "Unit tests for PKCE verifier", "kind": "test", "status": "pending" },
    { "id": "req-3", "description": "Existing OAuth tests still pass", "kind": "validation", "status": "pending" }
  ]
}
```

Also write `dod.md` at INTAKE with these items as unchecked boxes:

```markdown
# Definition of Done — <job_id>

Objective: <original_objective>

## Required Items

- [ ] req-1: PKCE flow implemented in auth.py
- [ ] req-2: Unit tests for PKCE verifier
- [ ] req-3: Existing OAuth tests still pass
```

As each item is completed during IMPLEMENT/VALIDATE, check it off in `dod.md` and
update `state.json.task.required_items[n].status = "complete"`.

### Auditor: verification at submission

The auditor reads `inbox/job.json → job.acceptance_criteria` and checks each item
against `dod.md`. See `modes/audit_only.md` Scenario A for the full protocol.

If `job.acceptance_criteria` is empty (legacy job or manual enqueue without criteria),
the auditor falls back to verifying `dod.md` has at least one checked item and
`validation_ledger.md` exists. This is a degraded check — prefer populating criteria.

---

## Coordinator/auditor session: `worker_submission_ready`

When the `worker_submission_ready` monitor event fires on a coordinator/auditor node:

1. Load `$SKILL_ROOT/modes/audit_only.md` — Scenario A.
2. Resolve the artifact directory:
   - Find the repo locally via `~/.prforge-mesh/config.json → worker.repo_roots`
   - Read `.prforge-run` pointer → artifact directory
3. Run the full Scenario A checklist from `audit_only.md`.
4. Write `mesh/auditor_verdict.json`.
5. Update `outbox/status.json` with `status: "auditor_verdict_written"`.

The coordinator Python daemon picks up the verdict and routes automatically:
- Both coordinator + auditor pass → moves to user approval (or manager mode)
- Auditor fail → writes `inbox/revision.json` to worker with structured fix instructions
- Auditor blocked → pauses job, notifies user

**Do NOT manually route the job.** Write the verdict and let the daemon handle routing.

---

## Worker: handling `revision_job_received`

When the monitor fires `revision_job_received`:

1. Read `inbox/revision.json`.
2. Read `revision.json.required_changes` — each entry has `check`, `instruction`, `how_to_fix`.
3. Read `revision.json.acceptance_criteria` — the original requirements are included.
4. For each required change:
   - Follow `how_to_fix` exactly
   - Update `dod.md` checkbox for the relevant criterion
   - Update `state.json.task.required_items[n].status`
5. After ALL changes addressed:
   - Re-run validation commands and update `validation_ledger.md`
   - Rewrite `hostile_review.md` with fresh answers
   - Regenerate `approval.md`
   - Set `outbox/status.json.status = "approval_ready"` to resubmit
6. The worker daemon detects `approval_ready`, writes `submission.json`, emits `WorkerSubmissionReady`.

`revision_count` in the revision packet tracks how many times this job has cycled.
After 3 revision cycles with no pass, transition to BLOCKED and notify the user.
