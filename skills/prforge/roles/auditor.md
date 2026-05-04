# PRForge Mesh Role: auditor

## Identity

The auditor runs on Machine 3. It polls GitHub for open PRs authored by the
configured user, classifies state changes, and enqueues jobs for workers.
It is read-only in every sense of the word.

## What the auditor does

```
Every poll_interval_minutes (default: 15 min):
  1. Identify authenticated gh user
  2. List open PRs authored by that user (lookback: last 3 days)
  3. For each PR in the lookback window:
       a. Read stored PR cursor state from Redis
       b. Compute current state fingerprints:
            current_head_sha     = PR.headRefOid
            current_rev_cursor   = latest submittedAt from external reviews only
            current_checks_hash  = stable normalized hash of all checks
       c. Compare to stored cursors
       d. If all three match: skip (skip-if-unchanged invariant)
       e. If review_cursor changed: enqueue review_response (P0 or P1)
       f. If checks_hash changed: classify CI failures, enqueue ci_fix if related/unknown
       g. If head_sha changed: enqueue audit_only if budget allows
       h. Update PR cursor state in Redis
```

## Cursor semantics — critical

### `last_review_cursor`
- Represents the `submittedAt` timestamp of the **latest already-processed external review**.
- Updated ONLY AFTER a `review_response` job is successfully queued.
- Never set from `PR.updatedAt`.
- Never updated for self-authored reviews.
- Self-authored reviews are filtered out at the `_fetch_reviews` level.

### `last_checks_hash`
- Stable normalized hash of CI check state.
- Normalized: sorted by check name, includes name/conclusion/status/url.
- Unknown check state ≠ previous known-good state (content differs = hash differs).
- Updated on every poll cycle where checks hash changed, regardless of whether a job was queued.

### `last_audited_head_sha`
- The head SHA of the PR at the time the last `audit_only` job was queued.
- Updated ONLY AFTER an `audit_only` job is successfully queued.
- Advances independently from `last_checks_hash` and `last_review_cursor`.

## Skip-if-unchanged invariant

Skip ALL classification if:
```
current_head_sha == last_audited_head_sha
AND current_review_cursor == last_review_cursor
AND current_checks_hash == last_checks_hash
```

If any one differs: classify the change, update cursor, enqueue relevant job.

## Review classification

```
CHANGES_REQUESTED  → P0 review_response
COMMENTED + body   → P1 review_response
APPROVED           → no job (unless it triggers a polish)
```

Dedup: `last_review_cursor` prevents the same review from queuing twice.
If auditor restarts, cursor is read from Redis — no duplicate from in-memory loss.

## CI classification

```
related   → check name/context contains a file stem from the PR diff
unrelated → known global infra pattern (network error, runner provision, etc.)
unknown   → cannot classify
```

- `related` → queue `ci_fix_related_to_branch P2`
- `unrelated` → emit `CIUnrelatedFailure` event, no job
- `unknown` → emit `CIUnknownFailure` event, include in job context alongside related
- Never silently drop unknown failures

## LLM audit rate limiting

Rate limit enforced in Redis, not in-memory. Survives daemon restart.

```
Workflow:<cluster>:audit_budget = sorted set
  score = Unix timestamp
  member = job_id
```

Check: `ZCOUNT ... (now-3600) now < max_llm_audits_per_hour`
Record: `ZADD ... now job_id`

If limit exceeded: emit `AuditSkippedBudgetLimit` event, notify, do not queue.

## `medium_idle_only` semantics

Medium-severity audit findings may only queue jobs when:
```
NO P0 or P1 jobs in pending stream
AND NO P0 or P1 active worker jobs
```

This is not "some worker is idle." It means zero high-priority pressure anywhere
in the pipeline. Medium findings never jump ahead of maintainer review feedback.

## What the auditor does NOT do

```
Does not:
  edit any file
  run git add, git commit, git push
  create pull requests
  post comments or review requests
  approve PRs
  request changes on PRs
  run gh pr create / gh pr comment / gh pr review
  consume a worker job slot
  run the coordinator loop
  run the worker loop
  assign jobs to other nodes
```

## Audit findings output (audit_only mode)

When an `audit_only` job runs (on this machine, read-only):

```
.prforge/audits/audit_<timestamp>.md   — human-readable findings
.prforge/audits/audit_findings.json    — validated against mesh_audit_finding.json schema
.prforge/outbox/result.json            — queue recommendation
```

Severity → job decision:
```
blocker → queue review_response P0
high    → queue pr_polish P3
medium  → queue pr_polish P3 (only if no P0/P1 pressure)
low     → record only
none    → record only
nit     → never queued
```

## Role check at startup

`prforge_mesh.py auditor` checks `"auditor" in config["mesh"]["roles"]` before
entering the loop. If not present, logs error and exits.

`config["worker"]["capacity"]` must be 0 on coordinator/auditor nodes.
The auditor will not run worker jobs regardless of what arrives in the stream.
