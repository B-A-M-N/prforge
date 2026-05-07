# PRForge Mesh MVP — Implementation Tracker

## Status

| Pass | Description | Status |
|------|-------------|--------|
| Pass 1 | Command, config, schema | DONE |
| Pass 2 | Redis backend | DONE |
| Pass 3 | Leases and queue | DONE |
| Pass 4 | Worker inbox + /pr-continue integration | DONE |
| Pass 5 | Auditor deterministic polling | DONE |
| Pass 6 | audit_only mode | DONE |
| Pass 7 | Notifications, docs, validation, systemd | DONE |

---

## Architecture Summary

Distributed wrapper around standalone PRForge. Does NOT alter standalone workflow.

```
Machine 1 (worker-1)          Machine 2 (worker-2)
  prforge-worker.service         prforge-worker.service
  SSH tunnel → 6380              SSH tunnel → 6380
         |                              |
         └──────────────┬───────────────┘
                        │ Redis Streams (durable)
                 Machine 3 (coordinator+auditor)
                   redis-server :6379 (localhost only)
                   prforge-coordinator.service
                   prforge-auditor.service
```

---

## Redis Key Space

**Prefix:** `Workflow:<cluster_name>:`

| Key | Type | Purpose |
|-----|------|---------|
| `Workflow:<c>:nodes` | SET | All registered node IDs |
| `Workflow:<c>:node:<node_id>` | HASH | Node state + heartbeat |
| `Workflow:<c>:job:<job_id>` | HASH | Job state |
| `Workflow:<c>:pr:<repo_slug>:<pr_number>` | HASH | PR audit cursor |
| `Workflow:<c>:stream:jobs:pending` | STREAM | Durable pending job queue |
| `Workflow:<c>:stream:jobs:active` | STREAM | Active job tracking |
| `Workflow:<c>:stream:events` | STREAM | Audit event log |
| `Workflow:<c>:lease:job:<job_id>` | STRING | Job lease (SET NX EX) |
| `Workflow:<c>:lease:target:<repo>:pr:<pr>` | STRING | PR/issue target uniqueness lock |
| `Workflow:<c>:lease:branch:<repo>:<branch>` | STRING | Branch uniqueness lock |
| `Workflow:<c>:lease:worker:<node_id>` | STRING | Worker busy lock |
| `Workflow:<c>:notify` | PUBSUB | Live notifications only |
| `Workflow:<c>:audit_budget` | ZSET | LLM audit timestamps (rate limiting, survives restart) |

---

## File Map

### Commands
- `commands/pr-distributed.md` — /pr-distributed command handler
- `commands/pr-continue.md` — MODIFIED: inbox job detection prepended

### Schemas (in skills/prforge/schemas/)
- `mesh_config.json` — ~/.prforge-mesh/config.json schema
- `mesh_node.json` — Redis node hash schema
- `mesh_job.json` — Redis job hash schema
- `mesh_audit_finding.json` — Audit finding + queue recommendation schema
- `policy_bundle.json` — worker-local capability envelope
- `intel_risk_signal.json` — local/mesh adaptive risk signal
- `policy_decision.json` — policy engine allow/warn/redirect/escalate result

### Mode
- `skills/prforge/modes/audit_only.md` — Read-only audit mode (Machine 3 only)

### Skill
- `skills/prforge/mesh.md` — Mesh activation/routing supplement

### Role specs (in skills/prforge/roles/)
- `worker.md` — Full worker daemon behavior spec
- `coordinator.md` — Full coordinator dispatch spec
- `auditor.md` — Full auditor polling spec (cursor semantics, rate limits, isolation)

### Scripts (in scripts/mesh/)
- `prforge_mesh.py` — Entry point (worker/coordinator/auditor/enqueue/status)
- `policy_engine.py` — Deterministic + adaptive policy decision engine
- `intel_engine.py` — FastEmbed local indexing, retrieval, reranking, and risk signal generation
- `redis_backend.py` — Redis connection, key helpers, stream ops, leases
- `coordinator.py` — Dispatch loop: node discovery, lease acquire, job assignment
- `worker.py` — Heartbeat loop: inbox write, phase reporting, lease renewal
- `auditor.py` — gh polling loop: PR detection, cursor tracking, job enqueue
- `notifications.py` — notify-send + Redis Pub/Sub publish
- `install_services.sh` — Systemd user service generator
- `validate_mesh.sh` — Acceptance test suite

### Docs
- `docs/MESH_IMPLEMENTATION.md` — This file

---

## Hard Constraints (Never Violate)

1. Standalone PRForge behavior unchanged — mesh wraps, never replaces
2. Redis prefix is `Workflow:` not `prforge:`
3. Machine 3 coordinator/auditor: read-only — no edit/commit/push/comment/PR
4. All public actions still require approval.md + /pr-approve
5. Run artifacts live outside repos under `~/.prforge/runs`; repo-local state is at most an ignored `.prforge-run` pointer
6. max_active_worker_jobs = 2 (global hard cap)
7. max_jobs_per_worker = 1
8. Same PR/issue target cannot hold two active target leases simultaneously
9. Worker leases must be acquired atomically — partial failure = release all
10. Pub/Sub for notifications only — Streams for durable dispatch
11. Intel/reranker output may increase caution or redirect, but may not bypass public-action safety
12. Workers may use cached local policy only inside the current active capability envelope

---

## Job Type → PRForge Mode Mapping

| Job Type | Mode File | Notes |
|----------|-----------|-------|
| `review_response` | modes/review_response.md | P0/P1 priority |
| `pr_polish` | modes/pr_polish.md | P3 |
| `ci_fix_related_to_branch` | modes/new_pr.md | CI-fix constraints applied |
| `audit_only` | modes/audit_only.md | Auditor-side, read-only |

---

## Priority Table

| Level | Trigger |
|-------|---------|
| P0 | Maintainer requested changes / blocking review |
| P1 | Maintainer comment/question needing response |
| P2 | Related CI failure on active PR branch |
| P3 | Auditor found high/medium issue (polish) |
| P4 | New PR work / candidate work |

Medium findings only queued when no P0/P1 pending. Low = recorded only. Nits = never queued.

---

## Config Locations

| Path | Purpose |
|------|---------|
| `~/.prforge-mesh/config.json` | Node config (validated against mesh_config.json schema) |
| `~/.prforge-mesh/mesh.env` | Env vars for systemd EnvironmentFile |
| `~/.prforge-mesh/logs/` | Service logs |
| `~/.prforge-mesh/services/` | Generated systemd unit copies |
| `~/.prforge/runs/<repo>/<branch-or-pr>/<run-id>/distributed.json` | Per-run mesh state |
| `~/.prforge/runs/<repo>/<branch-or-pr>/<run-id>/inbox/job.json` | Assigned job packet (triggers /pr-continue) |
| `~/.prforge/runs/<repo>/<branch-or-pr>/<run-id>/outbox/status.json` | Worker phase/status report |
| `~/.prforge/runs/<repo>/<branch-or-pr>/<run-id>/outbox/result.json` | Job completion result |
| `~/.prforge/runs/<repo>/<branch-or-pr>/<run-id>/audits/` | audit_only findings |
| `repo/.prforge-run` | Optional ignored pointer to the active outside-repo run |

Do not symlink `repo/.prforge` to `~/.prforge/runs/...`. Hooks must reject
symlinked PRForge state and block `.prforge/`, `.prforge-run`, and `.prforge-*`
from staging or tracking.

Set `PRFORGE_HOME` to override the outside artifact root. Default:
`$HOME/.prforge`.

---

## SSH Tunnel Setup (Workers)

```bash
# Machine 1 or 2 — connect to Machine 3 Redis
ssh -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -L 6380:127.0.0.1:6379 bamn@machine3

export PRFORGE_MESH_REDIS="redis://:PASSWORD@127.0.0.1:6380/0"
```

Machine 3 Redis must be bound to localhost only:
```conf
bind 127.0.0.1
protected-mode yes
requirepass YOUR_STRONG_PASSWORD
appendonly yes
```

---

## Acceptance Tests (validate_mesh.sh — tests A through J)

- PASS redis connection
- PASS redis stream write/read
- PASS lease acquire/release (SET NX)
- PASS A: PR cursor fields (all 7 present in HGETALL)
- PASS B: skip-if-unchanged (no changes = no job queued)
- PASS C: review cursor change → review_response P0/P1, no duplicate after advance
- PASS D: CI classification (related/unrelated/unknown) + hash stability
- PASS E: LLM audit budget (Redis ZSET, survives daemon restart)
- PASS F: medium_idle_only (P0/P1 pressure detection in stream + active jobs)
- PASS G: role isolation (_node_is_worker, auditor startup check)
- PASS H: duplicate target lease blocked, 4-lease atomic rollback
- PASS I: max 2 active worker jobs enforced
- PASS J: standalone /pr-continue no-op without inbox, worker packet schema
- PASS worker heartbeat
- PASS gh auth
- PASS auditor 3-day lookback filter
- PASS desktop notifications
