# PRForge Mesh Role: coordinator

## Identity

The coordinator is the central dispatcher for the mesh. It runs on Machine 3.
It reads the pending job stream, finds idle workers, acquires leases, and assigns jobs.
It is the only component that may write `status: assigned` to a job hash.

## What the coordinator does

```
Every 5 seconds:
  1. Read all registered nodes from Redis
  2. Filter to confirmed worker nodes only (role must contain "worker")
  3. Count active worker jobs (global cap = 2)
  4. If at cap: return immediately
  5. Find idle workers with capacity > 0
  6. Read pending job stream (sorted by priority, then created_at)
  7. For each job (P0 first):
       - Skip if type is audit_only (auditor handles it)
       - Skip if PR or branch already assigned this tick
       - Find eligible idle worker with matching allowed_modes
       - Acquire all 4 leases atomically (job + PR + branch + worker)
       - On lease failure: skip job, try next
       - On lease success: assign job, update node state, emit event, notify
```

## Role isolation — hard constraint

The coordinator MUST NOT assign jobs to nodes that do not have `"worker"` in their roles.

```
coordinator,auditor node  →  capacity = 0, never assigned worker jobs
worker node               →  capacity = 1, eligible for assignment
```

The coordinator checks `_node_is_worker()` before assignment. A node registering with
`roles: ["coordinator", "auditor"]` is never eligible for worker job dispatch even if
it appears in the node set.

## What the coordinator does NOT do

```
Does not:
  edit code
  run git commands
  push branches
  post comments
  create PRs
  poll GitHub for PR activity
  run the auditor loop
  run the worker loop
  handle audit_only jobs
  run in a node with capacity > 0
```

## Global hard limits

```
GLOBAL_MAX_ACTIVE_WORKER_JOBS = 2   (cannot be configured higher)
MAX_JOBS_PER_WORKER = 1             (enforced by worker lease)
Same PR cannot hold two active leases simultaneously
Same branch cannot hold two active leases simultaneously
```

These limits are enforced by code constants, not config values.

## Lease acquisition

All four leases must be acquired atomically before assignment:
- `Workflow:<cluster>:lease:job:<job_id>` — job ownership
- `Workflow:<cluster>:lease:pr:<repo>:<pr>` — PR uniqueness
- `Workflow:<cluster>:lease:branch:<repo>:<branch>` — branch uniqueness
- `Workflow:<cluster>:lease:worker:<node_id>` — worker busy

If any one fails: release all already-acquired, skip job, try next candidate.

## Stale job recovery

If a worker heartbeat expires (node key TTL expired):
- Coordinator removes the node from the active set
- Job lease TTL eventually expires (1800s default)
- On next tick, job is no longer "assigned" (lease gone)
- Coordinator sees it as "stale" and requeues it

## Priority dispatch order

```
P0 > P1 > P2 > P3 > P4
Within same priority: FIFO by created_at
```

## Role check at startup

`prforge_mesh.py coordinator` checks `"coordinator" in config["mesh"]["roles"]` before
entering the loop. If not present, it logs an error and exits.
