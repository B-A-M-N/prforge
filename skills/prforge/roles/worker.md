# PRForge Mesh Role: worker

## Identity

A worker node receives assigned PRForge jobs from the coordinator and runs the normal
standalone PRForge workflow locally. It does not poll GitHub, does not inspect other
nodes, and does not dispatch jobs to other machines.

## What a worker does

```
Every 15 seconds:
  1. Send heartbeat to Redis (Workflow:<cluster>:node:<node_id>)
  2. Read node state from Redis to detect assigned job
  3. If job assigned:
       - Resolve repo path from configured repo_roots
       - Write .prforge/inbox/job.json
       - Write .prforge/distributed.json
       - Update job status to "active"
       - Notify user (desktop + pubsub)
  4. If job active:
       - Read .prforge/outbox/status.json
       - Report phase/status to Redis job hash
       - Renew all four leases
  5. If job terminal (complete/failed/approval_ready/blocked):
       - Release all four leases
       - Reset node to idle
```

## What a worker does NOT do

```
Does not:
  poll GitHub for open PRs
  run gh pr list
  enqueue jobs
  dispatch jobs to other nodes
  run the auditor loop
  run the coordinator loop
  hold more than 1 active job at a time
  execute jobs if capacity = 0
```

## Capacity

`worker.capacity = 1` means this node can hold at most 1 active job.
`worker.capacity = 0` means this node is registered but accepts no jobs.
This is enforced by both the coordinator (lease check) and the worker itself.

## Role isolation enforcement

If the node config has `roles: ["coordinator", "auditor"]` but not `["worker"]`,
the worker service must not start. The `prforge_mesh.py worker` entry point
checks roles before entering the loop.

If `.prforge/distributed.json` exists and `role` is `coordinator` or `auditor`,
the worker should reject any non-audit_only job type.

## Allowed job types

```
review_response
pr_polish
ci_fix_related_to_branch
new_pr
```

`audit_only` is NOT a worker job type. The worker service ignores `audit_only` jobs.

## PRForge integration

When a job is assigned:

1. Worker writes `.prforge/inbox/job.json` in the resolved local repo path.
2. `/pr-continue` detects the inbox file at its next invocation.
3. `/pr-continue` reads the job packet, maps job.type to a PRForge mode, and runs the workflow.
4. All PRForge phase gates (SELF_REVIEW → PACKAGE → APPROVAL) apply exactly as in standalone mode.
5. No push, comment, or PR creation happens without explicit `/pr-approve`.

Worker writes `.prforge/outbox/status.json` after each phase transition to report progress.

## Lease renewal

While a job is active, the worker renews all four leases every heartbeat interval:
- `Workflow:<cluster>:lease:job:<job_id>`
- `Workflow:<cluster>:lease:pr:<repo>:<pr>`
- `Workflow:<cluster>:lease:branch:<repo>:<branch>`
- `Workflow:<cluster>:lease:worker:<node_id>`

If the worker process dies, leases expire (TTL 1800s) and the coordinator requeues the job.

## Heartbeat TTL

Node key TTL = `heartbeat_interval_seconds * 3` (e.g. 15s interval → 45s TTL).
If three consecutive heartbeats are missed, the node key expires and the coordinator
removes it from the active node set.
