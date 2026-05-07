"""
PRForge Mesh — coordinator loop.
Runs on Machine 3. Dispatches jobs to idle workers.
Does NOT execute worker jobs. Read/dispatch only.

Role isolation:
  - Only nodes with "worker" in their roles may receive worker jobs.
  - Nodes with only "coordinator" or "auditor" roles are NEVER assigned worker jobs.
  - audit_only jobs are not dispatched here — they run on the auditor node.
  - Coordinator refuses to run if this node does not have "coordinator" in its roles.

Manager Mode:
  On WorkerSubmissionReady event, the coordinator verifies the submission
  and writes coordinator_verdict.json. It does NOT execute public actions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure sibling modules (redis_backend, notifications) are importable
# when launched from systemd with WorkingDirectory set to scripts/mesh/
sys.path.insert(0, str(Path(__file__).parent))

import redis

from redis_backend import (
    acquire_job_leases,
    acquire_path_locks_atomic,
    emit_event,
    get_job,
    lease_path,
    list_nodes,
    normalize_path_for_lease,
    normalize_roles,
    read_pending_jobs,
    release_job_leases,
    release_path_locks,
    remove_from_pending,
    upsert_job,
)


def _read_pointer_artifact_dir(repo_path: str) -> Path:
    pointer = Path(repo_path) / ".prforge-run"
    if pointer.exists() and not pointer.is_symlink():
        data = {}
        for line in pointer.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
        if data.get("artifact_dir"):
            return Path(data["artifact_dir"])
    return Path(repo_path) / ".prforge"
from notifications import notify

from mesh_signing import sign_artifact, get_signing_key

logger = logging.getLogger("prforge.coordinator")

# Hard limits — enforced in code, not just config
GLOBAL_MAX_ACTIVE_WORKER_JOBS = 2
MAX_JOBS_PER_WORKER = 1

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}

# Job types that workers may execute. audit_only is auditor-side only.
WORKER_JOB_TYPES = {
    "review_response", "pr_polish", "ci_fix_related_to_branch", "new_pr",
}

# Sub-types that are coordinator-certified (require path lock acquisition)
MUTATING_JOB_TYPES = {"review_response", "pr_polish", "ci_fix_related_to_branch", "new_pr"}




def run(r: redis.Redis, cluster: str, config: dict) -> None:
    node_cfg   = config["mesh"]
    roles      = node_cfg.get("roles", [])
    limits     = config.get("limits", {})
    lease_ttl  = limits.get("lease_ttl_seconds", 1800)
    loop_interval = 5  # seconds between coordinator ticks

    # Role isolation: only coordinator role may run this loop
    if "coordinator" not in roles:
        logger.error(
            "Node role is %s — coordinator loop requires 'coordinator' role. Exiting.",
            roles,
        )
        return

    logger.info("Coordinator started for cluster=%s node=%s", cluster, node_cfg.get("node_id"))

    while True:
        try:
            _tick(r, cluster, lease_ttl, config)
        except Exception as e:
            logger.exception("Coordinator tick error: %s", e)
        time.sleep(loop_interval)


def _tick(r: redis.Redis, cluster: str, lease_ttl: int, config: dict | None = None) -> None:
    # 1. Read all nodes, collect only confirmed workers
    nodes = list_nodes(r, cluster)

    # Role isolation: a node is a worker only if it has EXACTLY "worker" in roles
    # A coordinator,auditor node is NEVER eligible to receive worker jobs
    workers = [
        n for n in nodes
        if _node_is_worker(n)
    ]

    # 2. Count active worker jobs (global cap — coordinator,auditor count excluded)
    active_worker_jobs = sum(
        1 for n in workers if n.get("status") == "active"
    )
    if active_worker_jobs >= GLOBAL_MAX_ACTIVE_WORKER_JOBS:
        logger.debug("Global cap reached: %d/%d active worker jobs",
                     active_worker_jobs, GLOBAL_MAX_ACTIVE_WORKER_JOBS)
        return

    # 3. Find idle workers with capacity
    idle_workers = [
        n for n in workers
        if n.get("status") == "idle" and int(n.get("capacity", 0)) > 0
    ]
    if not idle_workers:
        return

    # 4. Read pending jobs
    pending = read_pending_jobs(r, cluster)
    if not pending:
        return

    # 5. Sort by priority then created_at
    pending.sort(key=lambda j: (
        PRIORITY_ORDER.get(j.get("priority", "P4"), 99),
        j.get("created_at", ""),
    ))

    # Track what was assigned this tick to prevent double-dispatch
    assigned_prs:      set[str] = set()
    assigned_branches: set[str] = set()
    assigned_workers:  set[str] = set()
    assigned_paths:    set[str] = set()

    for job in pending:
        if active_worker_jobs >= GLOBAL_MAX_ACTIVE_WORKER_JOBS:
            break

        job_type   = job.get("type", "")
        job_status = job.get("status", "")

        # audit_only never dispatched to workers — auditor handles it locally
        if job_type not in WORKER_JOB_TYPES:
            logger.debug("Job type %s not dispatchable by coordinator — skipping", job_type)
            continue

        if job_status not in ("queued", "stale"):
            continue

        repo   = job.get("repo", "")
        pr     = str(job.get("pr_number", ""))
        branch = job.get("head_branch", "")
        pr_sig = f"{repo}:{pr}"
        br_sig = f"{repo}:{branch}"

        # 6. Enforce PR/branch uniqueness
        if pr_sig in assigned_prs or br_sig in assigned_branches:
            logger.debug("PR/branch already assigned this tick: %s / %s", pr_sig, br_sig)
            continue

        # 7. Find eligible worker — must have matching allowed_modes
        target_worker = None
        for worker in idle_workers:
            wid = worker["node_id"]
            if wid in assigned_workers:
                continue
            # Double-check role isolation: skip if this node is not a pure worker
            if not _node_is_worker(worker):
                logger.warning(
                    "Node %s appeared in workers list but role check failed — skipping",
                    wid,
                )
                continue
            allowed = _parse_allowed_modes(worker)
            if not _mode_allowed(job_type, allowed):
                continue
            target_worker = worker
            break

        if target_worker is None:
            logger.debug("No eligible worker for job %s type=%s", job.get("job_id"), job_type)
            continue

        wid = target_worker["node_id"]

        # 8. Acquire job/target/branch leases atomically
        ok, _ = acquire_job_leases(r, cluster, job, wid, lease_ttl)
        if not ok:
            logger.debug("Lease acquisition failed job=%s worker=%s", job.get("job_id"), wid)
            continue

        # 9. For mutating jobs, acquire path locks atomically before dispatch
        path_keys = []
        if job_type in MUTATING_JOB_TYPES:
            write_set = _resolve_write_set(job, repo)
            if write_set:
                # Check for same-file conflicts with already-assigned paths this tick
                conflict = False
                for p in write_set:
                    norm = normalize_path_for_lease(p)
                    if norm and norm in assigned_paths:
                        logger.debug(
                            "Path %s already assigned this tick — skipping job %s",
                            norm, job.get("job_id"),
                        )
                        conflict = True
                        break
                if conflict:
                    # Release job leases since we're not dispatching
                    _release_job_leases_safe(r, cluster, job, wid)
                    continue

                # Atomically acquire path locks via Lua
                repo_slug = repo.replace("/", "_")
                path_ok, blocked = acquire_path_locks_atomic(
                    r, cluster, repo_slug, wid, job["job_id"], write_set, lease_ttl,
                )
                if not path_ok:
                    logger.info(
                        "Path lock acquisition failed for job=%s worker=%s — "
                        "skipping dispatch. Blocked: %s",
                        job.get("job_id"), wid, blocked,
                    )
                    # Release job leases since we're not dispatching this job
                    _release_job_leases_safe(r, cluster, job, wid)
                    continue

                path_keys = [lease_path(cluster, repo_slug, normalize_path_for_lease(p))
                             for p in write_set if normalize_path_for_lease(p)]
                # Track assigned paths
                for p in write_set:
                    norm = normalize_path_for_lease(p)
                    if norm:
                        assigned_paths.add(norm)

        # 10. Assign job
        job_id = job["job_id"]
        now    = datetime.now(timezone.utc).isoformat()
        upsert_job(r, cluster, {
            **{k: v for k, v in job.items() if not k.startswith("_")},
            "status":        "assigned",
            "assigned_node": wid,
            "lease_id":      f"lease_{job_id}",
            "assigned_at":   now,
            "path_keys":     json.dumps(path_keys),
        })

        # Remove from pending stream
        if "_stream_id" in job:
            remove_from_pending(r, cluster, job["_stream_id"])

        # 11. Update worker node state
        r.hset(f"Workflow:{cluster}:node:{wid}", mapping={
            "status":     "active",
            "active_job": job_id,
        })

        emit_event(r, cluster, "JobDispatched", {
            "job_id":    job_id,
            "node":      wid,
            "repo":      repo,
            "pr_number": pr,
            "priority":  job.get("priority", "P4"),
        })
        notify(r, cluster, "JobDispatched",
               f"Job {job_id} → {wid} for {repo}#{pr}")

        active_worker_jobs += 1
        assigned_prs.add(pr_sig)
        assigned_branches.add(br_sig)
        assigned_workers.add(wid)

        logger.info("Dispatched job_id=%s worker=%s repo=%s pr=%s priority=%s",
                    job_id, wid, repo, pr, job.get("priority"))

    # Stale worker reaper: detect dead workers and requeue their jobs
    _reap_stale_workers(r, cluster, config)

    # Manager Mode: check for approval_ready jobs needing coordinator verdict
    if config is not None:
        _process_submission_ready_jobs(r, cluster, config)
        _poll_auditor_verdict_files(r, cluster, config)
        _process_audit_results(r, cluster, config)


def _resolve_write_set(job: dict, repo: str) -> list[str]:
    """Extract the declared write set from job constraints.

    Uses declared_write_set if present, falls back to allowed_paths.
    Directories are included (coordinator will resolve to files at IMPLEMENT time).
    """
    constraints = job.get("constraints", {})
    if isinstance(constraints, str):
        try:
            constraints = json.loads(constraints)
        except (json.JSONDecodeError, TypeError):
            constraints = {}

    write_set = constraints.get("declared_write_set")
    if write_set:
        return list(write_set)

    # Fallback to allowed_paths (logged as warning)
    allowed = constraints.get("allowed_paths", [])
    if allowed:
        logger.warning(
            "Job %s missing declared_write_set — falling back to allowed_paths",
            job.get("job_id"),
        )
        return list(allowed)

    return []


def _release_job_leases_safe(
    r: redis.Redis,
    cluster: str,
    job: dict,
    worker_id: str,
) -> None:
    """Release job/target/branch leases. Best-effort, logs errors."""
    try:
        release_job_leases(
            r, cluster,
            job["job_id"],
            job.get("repo", ""),
            str(job.get("pr_number", "")),
            job.get("head_branch", ""),
            worker_id,
        )
    except Exception:
        logger.exception("Failed to release job leases for job %s", job.get("job_id"))


def _reap_stale_workers(
    r: redis.Redis,
    cluster: str,
    config: dict | None,
) -> None:
    """
    Detect jobs assigned to dead/missing workers and requeue them.

    A worker is considered dead if its node hash has expired (no heartbeat)
    or its status is 'offline'. Jobs in 'assigned' or 'active' status are
    eligible for requeue. Jobs in 'approval_ready', 'complete', 'failed', or
    'blocked' are left alone — they are past the worker execution phase.

    Requeued jobs get retry_count incremented. Jobs exceeding max_requeues
    (default 3) are marked 'blocked' with reason 'max_requeues_exceeded'.
    """
    max_requeues = 3
    if config is not None:
        max_requeues = config.get("limits", {}).get("max_requeues", 3)

    # Scan all job keys in Redis
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor, match=f"Workflow:{cluster}:job:*", count=100)
        for jk in keys:
            job_data = r.hgetall(jk)
            if not job_data:
                continue

            job_status = job_data.get("status", "")
            if job_status not in ("assigned", "active"):
                continue

            job_id = job_data.get("job_id", "")
            assigned_node = job_data.get("assigned_node", "")
            if not assigned_node:
                continue

            # Check if the assigned worker node is alive
            node_data = r.hgetall(f"Workflow:{cluster}:node:{assigned_node}")
            node_missing = not node_data
            node_offline = node_data and node_data.get("status") == "offline"

            if not node_missing and not node_offline:
                continue

            # Worker is dead — requeue the job
            retry_count = int(job_data.get("retry_count", 0)) + 1

            if retry_count > max_requeues:
                logger.warning(
                    "Job %s exceeded max requeues (%d) — blocking",
                    job_id, max_requeues,
                )
                upsert_job(r, cluster, {
                    **{k: v for k, v in job_data.items()},
                    "status": "blocked",
                    "retry_count": str(retry_count),
                    "failure_reason": "max_requeues_exceeded",
                })
                emit_event(r, cluster, "JobBlocked", {
                    "job_id": job_id,
                    "reason": "max_requeues_exceeded",
                    "retry_count": str(retry_count),
                })
                notify(r, cluster, "JobBlocked",
                       f"Job {job_id} blocked after {retry_count} requeues (max={max_requeues})")
                continue

            # Release old leases
            repo = job_data.get("repo", "")
            pr_number = str(job_data.get("pr_number", ""))
            head_branch = job_data.get("head_branch", "")
            try:
                release_job_leases(r, cluster, job_id, repo, pr_number, head_branch, assigned_node)
            except Exception:
                logger.exception("Failed to release leases for stale job %s", job_id)

            # Requeue
            upsert_job(r, cluster, {
                **{k: v for k, v in job_data.items()},
                "status": "queued",
                "retry_count": str(retry_count),
                "assigned_node": "",
                "lease_id": "",
                "assigned_at": "",
            })

            emit_event(r, cluster, "WorkerOffline", {
                "node": assigned_node,
                "job_id": job_id,
                "retry_count": str(retry_count),
            })
            emit_event(r, cluster, "JobRequeued", {
                "job_id": job_id,
                "reason": "stale_worker_lost",
                "node": assigned_node,
                "retry_count": str(retry_count),
            })
            notify(r, cluster, "JobRequeued",
                   f"Job {job_id} requeue (retry {retry_count}/{max_requeues}) "
                   f"— worker {assigned_node} offline")
            logger.info(
                "Requeued stale job %s (retry %d/%d) — worker %s offline",
                job_id, retry_count, max_requeues, assigned_node,
            )

        if cursor == 0:
            break


def _process_submission_ready_jobs(
    r: redis.Redis,
    cluster: str,
    config: dict,
) -> None:
    """
    Find jobs with status=approval_ready that don't yet have a coordinator_verdict.
    For each, call handle_submission_ready() to verify and write verdict.
    """
    # Scan all jobs in Redis for approval_ready status
    # Use a pattern scan for job keys
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor, match=f"Workflow:{cluster}:job:*", count=100)
        for jk in keys:
            job_data = r.hgetall(jk)
            if job_data.get("status") == "approval_ready":
                job_id = job_data.get("job_id", "")
                if not job_id:
                    continue
                # Check if verdict already exists (avoid re-processing)
                repo = job_data.get("repo", "")
                pr_number = str(job_data.get("pr_number", ""))
                if not repo or not pr_number:
                    continue
                # Check if coordinator_verdict already written
                verdict_marker = f"Workflow:{cluster}:coordinator_verdict:{repo.replace('/', '_')}:{pr_number}"
                if r.exists(verdict_marker):
                    continue
                # Build job dict from hash
                job = {k: v for k, v in job_data.items()}
                try:
                    verdict = handle_submission_ready(r, cluster, job, config)
                    # Write verdict marker to prevent re-processing
                    r.set(verdict_marker, json.dumps(verdict), ex=3600)
                    # If coordinator passed, signal auditor Claude session via Redis
                    if verdict.get("decision") == "coordinator_pass":
                        audit_key = (
                            f"Workflow:{cluster}:audit_pending:"
                            f"{repo.replace('/', '_')}:{pr_number}"
                        )
                        r.setex(audit_key, 3600, json.dumps({
                            "job_id":     job_id,
                            "repo":       repo,
                            "pr_number":  pr_number,
                            "timestamp":  datetime.now(timezone.utc).isoformat(),
                        }))
                        logger.info("Set audit_pending for job=%s", job_id)
                except Exception as e:
                    logger.exception("handle_submission_ready failed for %s: %s", job_id, e)
        if cursor == 0:
            break


def _poll_auditor_verdict_files(
    r: redis.Redis,
    cluster: str,
    config: dict,
) -> None:
    """
    Scan approval_ready jobs that have coord_verdict but not auditor_verdict in Redis.
    If the worker's outbox/status.json shows auditor_verdict_written, read
    auditor_verdict.json and promote it to Redis so _process_audit_results can route.
    """
    worker_cfg = config.get("worker", {})
    repo_roots = worker_cfg.get("repo_roots", [])

    cursor = 0
    while True:
        cursor, keys = r.scan(cursor, match=f"Workflow:{cluster}:job:*", count=100)
        for jk in keys:
            job_data = r.hgetall(jk)
            if job_data.get("status") != "approval_ready":
                continue
            job_id    = job_data.get("job_id", "")
            repo      = job_data.get("repo", "")
            pr_number = str(job_data.get("pr_number", ""))
            if not job_id or not repo or not pr_number:
                continue

            audit_marker = (
                f"Workflow:{cluster}:auditor_verdict:{repo.replace('/', '_')}:{pr_number}"
            )
            if r.exists(audit_marker):
                continue  # already promoted

            coord_marker = (
                f"Workflow:{cluster}:coordinator_verdict:{repo.replace('/', '_')}:{pr_number}"
            )
            if not r.exists(coord_marker):
                continue  # coordinator hasn't passed yet

            repo_path = _resolve_repo_path(repo, repo_roots)
            if not repo_path:
                continue
            artifact_dir = _read_pointer_artifact_dir(repo_path)
            status_path  = artifact_dir / "outbox" / "status.json"
            verdict_path = artifact_dir / "mesh" / "auditor_verdict.json"

            try:
                if not status_path.exists() or not verdict_path.exists():
                    continue
                status_doc = json.loads(status_path.read_text())
                if status_doc.get("status") != "auditor_verdict_written":
                    continue
                verdict = json.loads(verdict_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue

            r.setex(audit_marker, 3600, json.dumps(verdict))
            logger.info("Promoted auditor_verdict to Redis for job=%s decision=%s",
                        job_id, verdict.get("decision"))
            emit_event(r, cluster, "AuditorVerdictPromoted", {
                "job_id":   job_id,
                "decision": verdict.get("decision", "unknown"),
            })

        if cursor == 0:
            break


def _process_audit_results(
    r: redis.Redis,
    cluster: str,
    config: dict,
) -> None:
    """
    Find jobs that have both coordinator_verdict and auditor_verdict but no
    manager_verdict yet. Routes the audit result:
      - auditor_pass: write manager_verdict (if manager mode enabled) or
                      move to approval_ready for user decision
      - auditor_fail: write revision job back to worker inbox with required changes
      - auditor_blocked: pause job and notify user
    """
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor, match=f"Workflow:{cluster}:job:*", count=100)
        for jk in keys:
            job_data = r.hgetall(jk)
            job_status = job_data.get("status", "")
            if job_status not in ("approval_ready",):
                continue

            job_id = job_data.get("job_id", "")
            repo = job_data.get("repo", "")
            pr_number = str(job_data.get("pr_number", ""))
            if not job_id or not repo or not pr_number:
                continue

            # Check if manager_verdict already written (avoid re-processing)
            mgr_marker = f"Workflow:{cluster}:manager_verdict:{repo.replace('/', '_')}:{pr_number}"
            if r.exists(mgr_marker):
                continue

            # Check if both coordinator and auditor verdicts exist
            coord_marker = f"Workflow:{cluster}:coordinator_verdict:{repo.replace('/', '_')}:{pr_number}"
            audit_marker = f"Workflow:{cluster}:auditor_verdict:{repo.replace('/', '_')}:{pr_number}"
            if not r.exists(coord_marker) or not r.exists(audit_marker):
                continue

            # Load verdicts
            try:
                coord_verdict = json.loads(r.get(coord_marker) or "{}")
                audit_verdict = json.loads(r.get(audit_marker) or "{}")
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse verdicts for %s", job_id)
                continue

            coord_pass = coord_verdict.get("all_pass", False)
            audit_pass = audit_verdict.get("all_pass", False)
            audit_decision = audit_verdict.get("decision", "auditor_fail")

            job = {k: v for k, v in job_data.items()}

            if coord_pass and audit_pass:
                # Both passed — route to manager or user approval
                _route_passed_audit(r, cluster, job, config, coord_verdict, audit_verdict)
            elif audit_decision == "auditor_blocked":
                # Blocked — pause and notify user
                _route_blocked_audit(r, cluster, job, audit_verdict)
            else:
                # Failed — write revision job back to worker
                _route_failed_audit(r, cluster, job, config, coord_verdict, audit_verdict)

        if cursor == 0:
            break


def _route_passed_audit(
    r: redis.Redis,
    cluster: str,
    job: dict,
    config: dict,
    coord_verdict: dict,
    audit_verdict: dict,
) -> None:
    """Both coordinator and auditor passed. Route to manager or user approval."""
    job_id = job.get("job_id", "")
    repo = job.get("repo", "")
    pr_number = str(job.get("pr_number", ""))
    node_id = job.get("assigned_node", "")

    # Check if manager mode is active
    mgr_cfg = config.get("manager_mode", {})
    if mgr_cfg.get("enabled", False) and mgr_cfg.get("authority") in ("low_risk_public", "internal_actions"):
        # Write manager_verdict
        _write_manager_verdict(r, cluster, job, config, coord_verdict, audit_verdict)
    else:
        # No manager mode — move to approval_ready for user decision
        upsert_job(r, cluster, {**job, "status": "approval_ready"})
        emit_event(r, cluster, "AuditPassed", {
            "job_id": job_id,
            "repo": repo,
            "pr_number": pr_number,
            "coordinator": "pass",
            "auditor": "pass",
            "next": "user_approval",
        })
        notify(r, cluster, "AuditPassed",
               f"Job {job_id} passed audit — ready for user approval on {node_id}")


def _route_blocked_audit(
    r: redis.Redis,
    cluster: str,
    job: dict,
    audit_verdict: dict,
) -> None:
    """Auditor blocked the job. Pause and notify user."""
    job_id = job.get("job_id", "")
    repo = job.get("repo", "")
    pr_number = str(job.get("pr_number", ""))

    upsert_job(r, cluster, {**job, "status": "blocked"})
    emit_event(r, cluster, "JobBlockedByAuditor", {
        "job_id": job_id,
        "repo": repo,
        "pr_number": pr_number,
        "reason": audit_verdict.get("failure_reason", "unknown"),
    })
    notify(r, cluster, "JobBlockedByAuditor",
           f"Job {job_id} blocked by auditor: {audit_verdict.get('failure_reason', 'unknown')}")


def _route_failed_audit(
    r: redis.Redis,
    cluster: str,
    job: dict,
    config: dict,
    coord_verdict: dict,
    audit_verdict: dict,
) -> None:
    """Audit failed. Convert findings into revision job and write to worker inbox."""
    job_id = job.get("job_id", "")
    repo = job.get("repo", "")
    pr_number = str(job.get("pr_number", ""))
    node_id = job.get("assigned_node", "")

    if not node_id:
        logger.error("Cannot route failed audit for %s — no assigned_node", job_id)
        return

    # Build revision findings from both verdicts
    revision_findings = _build_revision_findings(coord_verdict, audit_verdict)

    # Write revision.json to worker's artifact directory
    worker_cfg = config.get("worker", {})
    repo_roots = worker_cfg.get("repo_roots", [])
    repo_path = _resolve_repo_path(repo, repo_roots)

    if repo_path:
        artifact_dir = _read_pointer_artifact_dir(repo_path)
        inbox_dir = artifact_dir / "inbox"
        inbox_dir.mkdir(parents=True, exist_ok=True)

        def _parse_list(val, default: list) -> list:
            if isinstance(val, list):
                return val
            if isinstance(val, str):
                try:
                    import json as _json
                    parsed = _json.loads(val)
                    return parsed if isinstance(parsed, list) else default
                except (ValueError, TypeError):
                    return default
            return default

        revision_packet = {
            "job_id":             job_id,
            "repo":               repo,
            "pr_number":          pr_number,
            "audit_result":       "fail",
            "coordinator_pass":   coord_verdict.get("all_pass", False),
            "auditor_pass":       audit_verdict.get("all_pass", False),
            "revision_count":     int(job.get("revision_count", 0)) + 1,
            "required_changes":   revision_findings,
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            # Original acceptance criteria passed back so the worker can
            # understand WHAT was required, not just which check failed.
            "acceptance_criteria":  _parse_list(job.get("acceptance_criteria"), []),
            "original_objective":   job.get("objective", ""),
            "audit_guidance": (
                "Fix ALL items in required_changes before resubmitting. "
                "Each finding maps to a specific acceptance criterion. "
                "After fixing, rerun validation and regenerate approval.md, "
                "dod.md, and hostile_review.md before writing approval_ready "
                "to outbox/status.json."
            ),
        }

        revision_path = inbox_dir / "revision.json"
        revision_path.write_text(json.dumps(revision_packet, indent=2))
        logger.info("Wrote revision.json for job %s to %s", job_id, revision_path)

    # Update job status — keep as approval_ready so worker picks up revision
    upsert_job(r, cluster, {**job, "status": "approval_ready", "revision_count": str(int(job.get("revision_count", "0")) + 1)})

    emit_event(r, cluster, "RevisionJobWritten", {
        "job_id": job_id,
        "node": node_id,
        "findings": str(len(revision_findings)),
    })
    notify(r, cluster, "RevisionJobWritten",
           f"Revision job written for {job_id} — {len(revision_findings)} required changes")


def _build_revision_findings(
    coord_verdict: dict,
    audit_verdict: dict,
) -> list:
    """Convert coordinator and auditor verdict checks into structured revision findings."""
    findings = []

    # Process coordinator verdict failures
    coord_checks = coord_verdict.get("checks", {})
    for check_name, check_result in coord_checks.items():
        if not check_result.get("pass", True):
            findings.append({
                "source": "coordinator",
                "type": _map_coordinator_failure_type(check_name),
                "check": check_name,
                "detail": check_result.get("reason", ""),
                "instruction": _coordinator_failure_instruction(check_name, check_result),
            })

    # Process auditor verdict failures
    audit_checks = audit_verdict.get("checks", {})
    for check_name, check_result in audit_checks.items():
        if not check_result.get("pass", True):
            findings.append({
                "source": "auditor",
                "type": _map_auditor_failure_type(check_name),
                "check": check_name,
                "detail": check_result.get("reason", ""),
                "instruction": _auditor_failure_instruction(check_name, check_result),
            })

    return findings


def _map_coordinator_failure_type(check_name: str) -> str:
    """Map coordinator check failure to revision finding type."""
    mapping = {
        "lease_valid": "state_mismatch",
        "worker_owns_job": "state_mismatch",
        "worker_role": "state_mismatch",
        "pr_branch_locks": "state_mismatch",
        "global_limits": "state_mismatch",
        "job_type_compat": "state_mismatch",
        "artifacts": "evidence_missing",
    }
    return mapping.get(check_name, "state_mismatch")


def _map_auditor_failure_type(check_name: str) -> str:
    """Map auditor check failure to revision finding type."""
    mapping = {
        "diff_matches_approval": "scope_violation",
        "dod_evidence_valid": "evidence_missing",
        "validation_claims_supported": "validation_failure",
        "review_freshness_clean": "review_stale",
        "ci_relatedness_clean": "validation_failure",
        "unknown_ci_exists": "validation_failure",
        "scope_delta_clean": "scope_violation",
        "branch_drift_clean": "state_mismatch",
        "prforge_artifacts_not_staged": "state_mismatch",
        "commit_hygiene_clean": "state_mismatch",
        "public_text_preview_exists": "evidence_missing",
    }
    return mapping.get(check_name, "state_mismatch")


def _coordinator_failure_instruction(check_name: str, check_result: dict) -> str:
    """Generate human-readable instruction for coordinator check failure."""
    reason = check_result.get("reason", "")
    instructions = {
        "lease_valid": "Renew job lease before continuing. Contact coordinator.",
        "worker_owns_job": "Verify job assignment. Job may have been reassigned.",
        "worker_role": "This node is not authorized for this job type.",
        "pr_branch_locks": "Conflicting job exists for this PR/branch. Wait for release.",
        "global_limits": "System at capacity. Job will be retried automatically.",
        "job_type_compat": "Job type not supported by this worker.",
        "artifacts": f"Produce missing artifacts: {reason}",
    }
    return instructions.get(check_name, f"Fix coordinator check failure: {reason}")


def _auditor_failure_instruction(check_name: str, check_result: dict) -> str:
    """Generate human-readable instruction for auditor check failure."""
    reason = check_result.get("reason", "")
    instructions = {
        "diff_matches_approval": f"Reconcile diff with approval.md. Unapproved files changed: {reason}",
        "dod_evidence_valid": "Update dod.md with checked items and evidence.",
        "validation_claims_supported": f"Fix validation: {reason}",
        "review_freshness_clean": "Reviews are stale. Re-fetch and address new comments.",
        "ci_relatedness_clean": f"Fix related CI failures: {reason}",
        "unknown_ci_exists": f"Address unknown CI failures or document why they are unrelated: {reason}",
        "scope_delta_clean": f"Revert changes outside contract scope: {reason}",
        "branch_drift_clean": "Rebase on latest upstream. Local HEAD diverged from PR head.",
        "prforge_artifacts_not_staged": f"Unstage PRForge artifacts: {reason}",
        "commit_hygiene_clean": f"Fix commit messages: {reason}",
        "public_text_preview_exists": "Generate pr_body.md for approval.",
        "acceptance_criteria_met": (
            f"One or more acceptance criteria from the original job are not addressed in dod.md. "
            f"Re-read inbox/job.json, check job.acceptance_criteria, then update dod.md "
            f"with a checked [x] item for each criterion. Detail: {reason}"
        ),
    }
    return instructions.get(check_name, f"Fix auditor check failure: {reason}")


def _write_manager_verdict(
    r: redis.Redis,
    cluster: str,
    job: dict,
    config: dict,
    coord_verdict: dict,
    audit_verdict: dict,
) -> None:
    """Write manager_verdict.json when both coordinator and auditor pass."""
    from mesh_signing import sign_artifact, get_signing_key

    job_id = job.get("job_id", "")
    repo = job.get("repo", "")
    pr_number = str(job.get("pr_number", ""))

    mgr_cfg = config.get("manager_mode", {})
    authority = mgr_cfg.get("authority", "off")

    verdict = {
        "decision": "manager_pass" if authority != "off" else "manager_fail",
        "job_id": job_id,
        "repo": repo,
        "pr_number": pr_number,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coordinator_verdict_ref": f"Workflow:{cluster}:coordinator_verdict:{repo.replace('/', '_')}:{pr_number}",
        "auditor_verdict_ref": f"Workflow:{cluster}:auditor_verdict:{repo.replace('/', '_')}:{pr_number}",
        "authority": authority,
        "auto_public_actions": mgr_cfg.get("auto_public_actions", False),
        "allowed_public_actions": mgr_cfg.get("allowed_public_actions", []),
    }

    try:
        signing_key = get_signing_key()
        signed = sign_artifact(verdict, signing_key)
    except RuntimeError:
        signed = verdict

    # Write marker to Redis
    mgr_marker = f"Workflow:{cluster}:manager_verdict:{repo.replace('/', '_')}:{pr_number}"
    r.set(mgr_marker, json.dumps(signed), ex=3600)

    # Write to artifact directory
    worker_cfg = config.get("worker", {})
    repo_roots = worker_cfg.get("repo_roots", [])
    repo_path = _resolve_repo_path(repo, repo_roots)
    if repo_path:
        artifact_dir = _read_pointer_artifact_dir(repo_path)
        mesh_dir = artifact_dir / "mesh"
        mesh_dir.mkdir(parents=True, exist_ok=True)
        (mesh_dir / "manager_verdict.json").write_text(json.dumps(signed, indent=2))

    emit_event(r, cluster, "ManagerVerdictWritten", {
        "job_id": job_id,
        "decision": verdict["decision"],
        "authority": authority,
    })
    notify(r, cluster, "ManagerVerdictWritten",
           f"Manager verdict for {job_id}: {verdict['decision']} (authority={authority})")


# ---------------------------------------------------------------------------
# Role isolation helpers
# ---------------------------------------------------------------------------

def _node_is_worker(node: dict) -> bool:
    """
    A node is a worker only if "worker" is in its roles.
    Uses normalize_roles() — exact membership, no substring match.
    coordinator,auditor nodes are explicitly excluded.
    Returns False on any parse error (unknown role = not a worker).
    """
    roles_raw = node.get("roles", "")
    try:
        roles = normalize_roles(roles_raw)
    except ValueError:
        logger.warning("Node %s has invalid roles %r — not treating as worker",
                       node.get("node_id", "?"), roles_raw)
        return False
    return "worker" in roles


def _parse_allowed_modes(worker: dict) -> list:
    raw = worker.get("allowed_modes", "[]")
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _mode_allowed(job_type: str, allowed_modes: list) -> bool:
    mapping = {
        "review_response":          "review_response",
        "pr_polish":                "pr_polish",
        "ci_fix_related_to_branch": "ci_fix_related_to_branch",
        "new_pr":                   "new_pr",
    }
    required = mapping.get(job_type)
    if required is None:
        return False
    return required in allowed_modes


# ---------------------------------------------------------------------------
# Manager Mode — WorkerSubmissionReady handler
# ---------------------------------------------------------------------------

def handle_submission_ready(
    r: redis.Redis,
    cluster: str,
    job: dict,
    config: dict,
) -> dict:
    """
    Called when a worker reports WorkerSubmissionReady.
    Verifies the submission and writes coordinator_verdict.json.

    Checks:
      1. Lease valid (job lease exists and belongs to the worker)
      2. Worker owns the job (assigned_node matches)
      3. Worker role (node has 'worker' in roles)
      4. PR/branch locks (no conflicting leases)
      5. Global limits (active worker jobs <= cap)
      6. Job type / action compatibility
      7. Required artifacts and fingerprints exist

    Returns the verdict dict (signed).
    """
    from pathlib import Path

    job_id    = job.get("job_id", "")
    repo      = job.get("repo", "")
    pr_number = str(job.get("pr_number", ""))
    node_id   = job.get("assigned_node", "")
    head_branch = job.get("head_branch", "")

    checks: dict[str, dict] = {}
    all_pass = True

    # 1. Lease valid
    from redis_backend import lease_job, lease_pr
    job_lease_val = r.get(lease_job(cluster, job_id))
    lease_valid = job_lease_val == node_id
    checks["lease_valid"] = {"pass": lease_valid, "reason": "" if lease_valid else f"job lease held by {job_lease_val}, not {node_id}"}
    if not lease_valid:
        all_pass = False

    # 2. Worker owns job
    job_data = get_job(r, cluster, job_id)
    assigned_node = job_data.get("assigned_node", "") if job_data else ""
    worker_owns = assigned_node == node_id
    checks["worker_owns_job"] = {"pass": worker_owns, "reason": "" if worker_owns else f"assigned_node={assigned_node}, reporter={node_id}"}
    if not worker_owns:
        all_pass = False

    # 3. Worker role
    node_data = r.hgetall(f"Workflow:{cluster}:node:{node_id}")
    node_roles_raw = node_data.get("roles", "") if node_data else ""
    try:
        node_roles = normalize_roles(node_roles_raw)
    except ValueError:
        node_roles = []
    has_worker_role = "worker" in node_roles
    checks["worker_role"] = {"pass": has_worker_role, "reason": "" if has_worker_role else f"node roles={node_roles}, missing 'worker'"}
    if not has_worker_role:
        all_pass = False

    # 4. PR/branch locks — verify no conflicting leases
    pr_lease_val = r.get(lease_pr(cluster, repo, pr_number))
    br_lease_val = r.get(branch_lease_key(cluster, repo, head_branch)) if head_branch else None
    pr_locked = pr_lease_val == job_id
    br_locked = (br_lease_val == job_id) if head_branch else True
    locks_ok = pr_locked and br_locked
    lock_reasons = []
    if not pr_locked:
        lock_reasons.append(f"PR lease held by {pr_lease_val}")
    if not br_locked:
        lock_reasons.append(f"branch lease held by {br_lease_val}")
    checks["pr_branch_locks"] = {"pass": locks_ok, "reason": "; ".join(lock_reasons)}
    if not locks_ok:
        all_pass = False

    # 5. Global limits
    nodes = list_nodes(r, cluster)
    workers = [n for n in nodes if _node_is_worker(n)]
    active_count = sum(1 for n in workers if n.get("status") == "active")
    within_limits = active_count <= GLOBAL_MAX_ACTIVE_WORKER_JOBS
    checks["global_limits"] = {"pass": within_limits, "reason": "" if within_limits else f"active={active_count} >= cap={GLOBAL_MAX_ACTIVE_WORKER_JOBS}"}
    if not within_limits:
        all_pass = False

    # 6. Job type / action compatibility
    job_type = job.get("type", "")
    type_ok = job_type in WORKER_JOB_TYPES
    checks["job_type_compat"] = {"pass": type_ok, "reason": "" if type_ok else f"type={job_type} not in {WORKER_JOB_TYPES}"}
    if not type_ok:
        all_pass = False

    # 7. Required artifacts and fingerprints exist
    worker_cfg = config.get("worker", {})
    repo_roots = worker_cfg.get("repo_roots", [])
    repo_path = _resolve_repo_path(repo, repo_roots)
    artifacts_ok = True
    artifact_reasons = []
    if repo_path:
        artifact_dir = _read_pointer_artifact_dir(repo_path)
        mesh_dir = artifact_dir / "mesh"
        outbox_dir = artifact_dir / "outbox"
        required_files = [
            outbox_dir / "submission.json",
            artifact_dir / "approval.md",
            artifact_dir / "dod.md",
        ]
        for rf in required_files:
            if not rf.exists():
                artifacts_ok = False
                artifact_reasons.append(f"missing: {rf.name}")
        # Verify submission.json has fingerprints
        sub_path = outbox_dir / "submission.json"
        if sub_path.exists():
            try:
                sub = json.loads(sub_path.read_text())
                if not sub.get("diff_hash"):
                    artifacts_ok = False
                    artifact_reasons.append("submission.json missing diff_hash")
            except (json.JSONDecodeError, OSError):
                artifacts_ok = False
                artifact_reasons.append("submission.json unreadable")
    else:
        artifacts_ok = False
        artifact_reasons.append(f"repo {repo} not found in roots {repo_roots}")

    checks["artifacts"] = {"pass": artifacts_ok, "reason": "; ".join(artifact_reasons)}
    if not artifacts_ok:
        all_pass = False

    # Build verdict
    decision = "coordinator_pass" if all_pass else "coordinator_fail"
    verdict: dict = {
        "decision": decision,
        "job_id": job_id,
        "node_id": node_id,
        "repo": repo,
        "pr_number": pr_number,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "all_pass": all_pass,
    }
    if not all_pass:
        failed = [k for k, v in checks.items() if not v["pass"]]
        verdict["failure_reason"] = ", ".join(failed)

    # Sign and write
    try:
        signing_key = get_signing_key()
        signed = sign_artifact(verdict, signing_key)
    except RuntimeError:
        logger.warning("PRFORGE_MESH_SIGNING_KEY not set — writing unsigned verdict")
        signed = verdict

    if repo_path:
        mesh_dir = _read_pointer_artifact_dir(repo_path) / "mesh"
        mesh_dir.mkdir(parents=True, exist_ok=True)
        (mesh_dir / "coordinator_verdict.json").write_text(json.dumps(signed, indent=2))
        logger.info(" coordinator_verdict.json for jobWrote=%s decision=%s", job_id, decision)

    emit_event(r, cluster, "CoordinatorVerdictWritten", {
        "job_id": job_id,
        "decision": decision,
        "node_id": node_id,
    })
    notify(r, cluster, "CoordinatorVerdictWritten",
           f"Coordinator verdict for {job_id}: {decision}")

    return signed


def branch_lease_key(cluster: str, repo: str, branch: str) -> str:
    """Helper to build branch lease key (same logic as redis_backend.lease_branch)."""
    safe = branch.replace("/", "_")
    return f"Workflow:{cluster}:lease:branch:{repo.replace('/', '_')}:{safe}"


def _resolve_repo_path(repo_slug: str, repo_roots: list) -> str | None:
    """Find local path for a given org/repo slug under configured roots."""
    import os
    repo_name = repo_slug.split("/")[-1]
    for root in repo_roots:
        candidates = [
            os.path.join(root, repo_name),
            os.path.join(root, repo_slug),
        ]
        for candidate in candidates:
            if os.path.isdir(os.path.join(candidate, ".git")):
                return candidate
    return None
