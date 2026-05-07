"""
PRForge Mesh — Redis backend.
All Redis operations go through this module.
Key prefix: Workflow:<cluster_name>:
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import redis

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def connect(redis_url: Optional[str] = None) -> redis.Redis:
    url = redis_url or os.environ.get("PRFORGE_MESH_REDIS")
    if not url:
        raise RuntimeError(
            "Redis URL not set. Set PRFORGE_MESH_REDIS or pass redis_url.\n"
            "Example: redis://:PASSWORD@127.0.0.1:6380/0"
        )
    r = redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        health_check_interval=30,
    )
    r.ping()
    return r


# ---------------------------------------------------------------------------
# Key helpers — all keys use Workflow: prefix
# ---------------------------------------------------------------------------

def key(cluster: str, *parts: str) -> str:
    return "Workflow:" + cluster + ":" + ":".join(parts)


def nodes_key(c: str) -> str:
    return key(c, "nodes")

def node_key(c: str, node_id: str) -> str:
    return key(c, "node", node_id)

def job_key(c: str, job_id: str) -> str:
    return key(c, "job", job_id)

def pr_key(c: str, repo_slug: str, pr_number: str) -> str:
    return key(c, "pr", repo_slug.replace("/", "_"), pr_number)

def stream_pending(c: str) -> str:
    return key(c, "stream", "jobs", "pending")

def stream_active(c: str) -> str:
    return key(c, "stream", "jobs", "active")

def stream_events(c: str) -> str:
    return key(c, "stream", "events")

def lease_job(c: str, job_id: str) -> str:
    return key(c, "lease", "job", job_id)

def lease_pr(c: str, repo_slug: str, pr_number: str) -> str:
    return key(c, "lease", "pr", repo_slug.replace("/", "_"), pr_number)

def lease_branch(c: str, repo_slug: str, branch: str) -> str:
    safe = branch.replace("/", "_")
    return key(c, "lease", "branch", repo_slug.replace("/", "_"), safe)

def lease_worker(c: str, node_id: str) -> str:
    return key(c, "lease", "worker", node_id)

def lease_path(c: str, repo_slug: str, rel_path: str) -> str:
    safe = rel_path.replace("\\", "/").lstrip("/")
    return key(c, "lease", "path", repo_slug.replace("/", "_"), safe)

def lease_target(c: str, repo_slug: str, target_type: str, target_number: str) -> str:
    return key(c, "lease", "target", repo_slug.replace("/", "_"), target_type, target_number)

def lease_public(c: str, repo_slug: str, branch_or_pr: str) -> str:
    safe = branch_or_pr.replace("/", "_")
    return key(c, "lease", "public", repo_slug.replace("/", "_"), safe)

def notify_channel(c: str) -> str:
    return key(c, "notify")


# ---------------------------------------------------------------------------
# Node operations
# ---------------------------------------------------------------------------

def heartbeat(r: redis.Redis, cluster: str, node: dict, ttl: int = 45) -> None:
    nk = node_key(cluster, node["node_id"])
    r.hset(nk, mapping={
        "node_id":    node["node_id"],
        "roles":      node.get("roles", ""),
        "status":     node.get("status", "idle"),
        "capacity":   str(node.get("capacity", 0)),
        "active_job": node.get("active_job", ""),
        "last_seen":  _now(),
        "repo_roots": json.dumps(node.get("repo_roots", [])),
        "version":    "mesh-mvp",
    })
    r.expire(nk, ttl)
    r.sadd(nodes_key(cluster), node["node_id"])


def get_node(r: redis.Redis, cluster: str, node_id: str) -> Optional[dict]:
    nk = node_key(cluster, node_id)
    data = r.hgetall(nk)
    return data if data else None


def list_nodes(r: redis.Redis, cluster: str) -> list[dict]:
    ids = r.smembers(nodes_key(cluster))
    nodes = []
    for nid in ids:
        n = get_node(r, cluster, nid)
        if n:
            nodes.append(n)
        else:
            # Expired — remove from set
            r.srem(nodes_key(cluster), nid)
    return nodes


def mark_offline(r: redis.Redis, cluster: str, node_id: str) -> None:
    nk = node_key(cluster, node_id)
    r.hset(nk, "status", "offline")


# ---------------------------------------------------------------------------
# Job operations
# ---------------------------------------------------------------------------

def upsert_job(r: redis.Redis, cluster: str, job: dict) -> None:
    jk = job_key(cluster, job["job_id"])
    flat = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v))
            for k, v in job.items()}
    r.hset(jk, mapping=flat)


def get_job(r: redis.Redis, cluster: str, job_id: str) -> Optional[dict]:
    jk = job_key(cluster, job_id)
    data = r.hgetall(jk)
    return data if data else None


def enqueue_job(r: redis.Redis, cluster: str, job: dict) -> str:
    """Add job to pending stream and create job hash."""
    upsert_job(r, cluster, job)
    r.xadd(stream_pending(cluster), {
        "job_id":              job["job_id"],
        "priority":            job["priority"],
        "type":                job["type"],
        "repo":                job["repo"],
        "pr_number":           str(job["pr_number"]),
        "source_url":          job.get("source_url", ""),
        "objective":           job.get("objective", job.get("original_objective", "")),
        "acceptance_criteria": json.dumps(job.get("acceptance_criteria", [])),
    })
    emit_event(r, cluster, "JobQueued", {
        "job_id":    job["job_id"],
        "repo":      job["repo"],
        "pr_number": str(job["pr_number"]),
        "type":      job["type"],
        "priority":  job["priority"],
    })
    return job["job_id"]


def read_pending_jobs(r: redis.Redis, cluster: str, count: int = 50) -> list[dict]:
    """Read from pending stream (do not ack — coordinator reads and moves manually)."""
    entries = r.xrange(stream_pending(cluster), count=count)
    jobs = []
    for entry_id, fields in entries:
        jid = fields.get("job_id")
        if jid:
            job = get_job(r, cluster, jid)
            if job:
                job["_stream_id"] = entry_id
                jobs.append(job)
    return jobs


def remove_from_pending(r: redis.Redis, cluster: str, stream_id: str) -> None:
    r.xdel(stream_pending(cluster), stream_id)


# ---------------------------------------------------------------------------
# Lease operations
# ---------------------------------------------------------------------------

def normalize_path_for_lease(path: str) -> str:
    """Normalize a file path for lease key matching. Returns empty string for invalid paths."""
    p = path.replace("\\", "/").lstrip("/")
    if ".." in p or p.startswith("/"):
        return ""
    return p


def _lease_value(worker_id: str, job_id: str, repo: str = "", path: str = "") -> str:
    """Build JSON lease value."""
    return json.dumps({
        "worker_id": worker_id,
        "job_id": job_id,
        "repo": repo,
        "path": path,
        "created_at": _now(),
    })


def acquire_lease(r: redis.Redis, lease_k: str, value: str, ttl: int) -> bool:
    result = r.set(lease_k, value, nx=True, ex=ttl)
    return result is True


def release_lease(r: redis.Redis, lease_k: str) -> None:
    r.delete(lease_k)


def renew_lease(r: redis.Redis, lease_k: str, ttl: int) -> bool:
    return r.expire(lease_k, ttl) == 1


def get_lease(r: redis.Redis, lease_k: str) -> Optional[dict]:
    """Get lease value as dict, or None if not found."""
    val = r.get(lease_k)
    if val is None:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


def acquire_path_leases(
    r: redis.Redis,
    cluster: str,
    repo_slug: str,
    paths: list[str],
    worker_id: str,
    job_id: str,
    ttl: int = 600,
) -> tuple[bool, list[str]]:
    """
    All-or-nothing path lease acquisition.
    If any path lease fails, releases all acquired in this call.
    Returns (success, list_of_blocked_paths).
    """
    acquired = []
    for rel_path in paths:
        # Normalize path
        norm = rel_path.replace("\\", "/").lstrip("/")
        if ".." in norm or norm.startswith("/"):
            # Skip invalid paths — they won't match anything real
            continue
        lk = lease_path(cluster, repo_slug, norm)
        val = _lease_value(worker_id, job_id, repo=repo_slug, path=norm)
        if acquire_lease(r, lk, val, ttl):
            acquired.append(lk)
        else:
            # Rollback
            for ak in acquired:
                release_lease(r, ak)
            holder = get_lease(r, lk)
            blocked_by = holder.get("worker_id", "?") if holder else "?"
            return False, [rel_path, blocked_by]
    return True, []


def acquire_job_leases(
    r: redis.Redis,
    cluster: str,
    job: dict,
    node_id: str,
    ttl: int,
) -> tuple[bool, list[str]]:
    """
    Atomically acquire job, target, branch, and worker leases via Redis Lua.
    Returns (success, list_of_acquired_keys). On failure no partial locks remain.
    """
    job_id    = job["job_id"]
    repo      = job["repo"]
    pr        = str(job.get("pr_number", ""))
    branch    = job.get("head_branch", "")
    repo_slug = repo.replace("/", "_")

    leases = [
        (lease_job(cluster, job_id),
         _lease_value(node_id, job_id, repo=repo)),
        (lease_target(cluster, repo_slug, "pr", pr),
         _lease_value(node_id, job_id, repo=repo)),
        (lease_branch(cluster, repo_slug, branch),
         _lease_value(node_id, job_id, repo=repo)),
        (lease_worker(cluster, node_id),
         _lease_value(node_id, job_id, repo=repo)),
    ]

    keys = [lk for lk, _lv in leases]
    args = [lv for _lk, lv in leases]
    args.append(str(ttl))
    result = r.eval(ACQUIRE_PATH_LOCKS_LUA, len(keys), *(keys + args))
    if result[0] == 1:
        return True, keys
    return False, []


def renew_job_leases(
    r: redis.Redis,
    cluster: str,
    job_id: str,
    repo: str,
    pr: str,
    branch: str,
    node_id: str,
    ttl: int,
) -> list[str]:
    repo_slug = repo.replace("/", "_")
    failed = []
    for lk in [
        lease_job(cluster, job_id),
        lease_target(cluster, repo_slug, "pr", pr),
        lease_branch(cluster, repo_slug, branch),
        lease_worker(cluster, node_id),
    ]:
        result = r.eval(RENEW_LOCK_LUA, 1, lk, node_id, job_id, str(ttl))
        if result == 0:
            failed.append(lk)
    return failed


def release_job_leases(
    r: redis.Redis,
    cluster: str,
    job_id: str,
    repo: str,
    pr: str,
    branch: str,
    node_id: str,
) -> list[str]:
    repo_slug = repo.replace("/", "_")
    failed = []
    for lk in [
        lease_job(cluster, job_id),
        lease_target(cluster, repo_slug, "pr", pr),
        lease_branch(cluster, repo_slug, branch),
        lease_worker(cluster, node_id),
    ]:
        result = r.eval(RELEASE_LOCK_LUA, 1, lk, node_id, job_id)
        if result == 0:
            failed.append(lk)
    return failed


# ---------------------------------------------------------------------------
# List all locks for a cluster (for status/debugging)
# ---------------------------------------------------------------------------

def list_all_leases(r: redis.Redis, cluster: str, prefix: str = "lease") -> list[dict]:
    """List all lease keys matching Workflow:<cluster>:<prefix>:*."""
    pattern = key(cluster, prefix, "*")
    results = []
    for k in r.scan_iter(match=pattern, count=100):
        val = get_lease(r, k)
        if val:
            results.append({"key": k, **val})
    return results


# ---------------------------------------------------------------------------
# PR cursor (auditor)
# ---------------------------------------------------------------------------

# Required PR cursor fields — never omit, store "" if unknown
# last_review_cursor    = last PROCESSED review cursor (advanced after enqueue)
# last_observed_review_cursor = last SEEN external review timestamp (for debugging)
PR_CURSOR_FIELDS = [
    "head_sha",
    "updated_at",
    "last_audited_head_sha",
    "last_audited_at",
    "last_review_cursor",
    "last_observed_review_cursor",
    "last_checks_hash",
    "last_audit_severity",
]


def get_pr_cursor(r: redis.Redis, cluster: str, repo: str, pr_number: str) -> dict:
    pk = pr_key(cluster, repo, pr_number)
    data = r.hgetall(pk)
    if not data:
        return {}
    # Backfill missing required fields with explicit empty string
    for field in PR_CURSOR_FIELDS:
        if field not in data:
            data[field] = ""
    return data


def update_pr_cursor(r: redis.Redis, cluster: str, repo: str, pr_number: str, fields: dict) -> None:
    pk = pr_key(cluster, repo, pr_number)
    # Always write all required fields; missing ones default to ""
    complete: dict = {f: "" for f in PR_CURSOR_FIELDS}
    complete.update({k: str(v) for k, v in fields.items()})
    r.hset(pk, mapping=complete)


# ---------------------------------------------------------------------------
# Role normalization — safe parsing of roles from Redis hash or config
# ---------------------------------------------------------------------------

VALID_ROLES = {"worker", "coordinator", "auditor"}


def normalize_roles(value) -> list[str]:
    """
    Parse roles from a Redis hash field (string) or config (list).
    Accepts:
      - Python list: ["worker", "auditor"]
      - JSON array string: '["worker","auditor"]'
      - Comma-separated string: "worker,auditor" or "worker"
    Rejects unknown roles (raises ValueError).
    Returns a sorted, deduplicated list of validated role strings.
    Never matches by substring — only exact membership.
    """
    if isinstance(value, list):
        parts = [str(v).strip() for v in value]
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            import json as _json
            try:
                parsed = _json.loads(stripped)
                parts = [str(v).strip() for v in parsed]
            except (ValueError, TypeError) as e:
                raise ValueError(f"Invalid JSON roles array: {stripped!r}") from e
        else:
            parts = [p.strip() for p in stripped.split(",") if p.strip()]
    else:
        raise ValueError(f"Unsupported roles type: {type(value).__name__}")

    unknown = [p for p in parts if p not in VALID_ROLES]
    if unknown:
        raise ValueError(f"Unknown role(s): {unknown}. Valid: {sorted(VALID_ROLES)}")

    return sorted(set(parts))


# ---------------------------------------------------------------------------
# LLM audit budget (Redis-backed rate limiter — survives daemon restart)
# ---------------------------------------------------------------------------

def audit_budget_key(c: str) -> str:
    return key(c, "audit_budget")


def audit_budget_count(r: redis.Redis, cluster: str) -> int:
    """Return number of LLM audits recorded in the last hour."""
    import time
    now  = time.time()
    hour_ago = now - 3600
    # Prune entries older than 1 hour
    r.zremrangebyscore(audit_budget_key(cluster), "-inf", hour_ago)
    return r.zcard(audit_budget_key(cluster))


def audit_budget_record(r: redis.Redis, cluster: str, job_id: str) -> None:
    """Record an LLM audit in the budget sorted set."""
    import time
    now = time.time()
    r.zadd(audit_budget_key(cluster), {job_id: now})
    # Keep the sorted set from growing forever — cap at 1000 entries
    r.zremrangebyrank(audit_budget_key(cluster), 0, -1001)


def audit_budget_under_limit(r: redis.Redis, cluster: str, max_per_hr: int) -> bool:
    """Return True if another LLM audit is allowed within the hourly budget."""
    if max_per_hr <= 0:
        return False  # 0 = disabled entirely
    return audit_budget_count(r, cluster) < max_per_hr


# ---------------------------------------------------------------------------
# P0/P1 pressure check (for medium_idle_only enforcement)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Enqueue dedupe guard — prevents duplicate jobs from crash-restart race
# ---------------------------------------------------------------------------

def dedupe_key(c: str, event_type: str, repo: str, pr_number: str, fingerprint: str) -> str:
    """
    Dedupe key encodes a specific change event.
    event_type: "review" | "ci" | "audit"
    fingerprint: short identifier from the cursor value (review_ts or checks_hash prefix).
    TTL must be >= 2x auditor poll_interval to survive one full missed cycle.
    """
    slug = repo.replace("/", "_")
    return key(c, "dedupe", event_type, slug, pr_number, fingerprint[:16])


def try_acquire_enqueue_dedupe(
    r: redis.Redis,
    cluster: str,
    event_type: str,
    repo: str,
    pr_number: str,
    fingerprint: str,
    job_id: str,
    ttl: int = 1800,
) -> bool:
    """
    Acquire a dedupe lock for this (repo, pr, event_type, fingerprint) tuple.
    Returns True if the lock was acquired (safe to enqueue).
    Returns False if already locked (job already enqueued for this event — skip).
    """
    dk = dedupe_key(cluster, event_type, repo, pr_number, fingerprint)
    result = r.set(dk, job_id, nx=True, ex=ttl)
    return result is True


def has_high_priority_pressure(r: redis.Redis, cluster: str) -> bool:
    """
    Return True if there are any P0 or P1 jobs pending OR active.
    medium_idle_only = no P0/P1 pressure anywhere in the pipeline,
    NOT just "some worker is idle".
    """
    # Check pending stream
    entries = r.xrange(stream_pending(cluster), count=100)
    for _eid, fields in entries:
        if fields.get("priority") in ("P0", "P1"):
            return True
    # Check active worker nodes
    nodes = list_nodes(r, cluster)
    for node in nodes:
        if node.get("status") == "active" and node.get("active_job"):
            job_id = node["active_job"]
            job = get_job(r, cluster, job_id)
            if job and job.get("priority") in ("P0", "P1"):
                return True
    return False


# ---------------------------------------------------------------------------
# Event stream
# ---------------------------------------------------------------------------

def emit_event(r: redis.Redis, cluster: str, event: str, fields: dict) -> None:
    payload = {"event": event, "ts": _now()}
    payload.update(fields)
    r.xadd(stream_events(cluster), payload)


# ---------------------------------------------------------------------------
# Status summary
# ---------------------------------------------------------------------------

def mesh_status(r: redis.Redis, cluster: str) -> dict:
    nodes = list_nodes(r, cluster)
    pending_count = r.xlen(stream_pending(cluster))
    active_count = sum(
        1 for n in nodes
        if n.get("status") == "active" and "worker" in n.get("roles", "")
    )
    return {
        "cluster": cluster,
        "nodes": nodes,
        "pending_jobs": pending_count,
        "active_worker_jobs": active_count,
    }


# ---------------------------------------------------------------------------
# Lua scripts for atomic multi-key operations
# ---------------------------------------------------------------------------

ACQUIRE_PATH_LOCKS_LUA = """
local ttl = ARGV[#ARGV]
local acquired = {}
for i, key in ipairs(KEYS) do
  local ok = redis.call('SET', key, ARGV[i], 'NX', 'EX', ttl)
  if ok then
    table.insert(acquired, key)
  else
    for _, ak in ipairs(acquired) do
      redis.call('DEL', ak)
    end
    local existing = redis.call('GET', key)
    return {0, key, existing or ''}
  end
end
return {1, "", ""}
"""

RENEW_LOCK_LUA = """
local current = redis.call('GET', KEYS[1])
if not current then return 0 end
local decoded = cjson.decode(current)
if decoded.worker_id == ARGV[1] and decoded.job_id == ARGV[2] then
  return redis.call('EXPIRE', KEYS[1], ARGV[3])
end
return 0
"""

RELEASE_LOCK_LUA = """
local current = redis.call('GET', KEYS[1])
if not current then return 1 end
local decoded = cjson.decode(current)
if decoded.worker_id == ARGV[1] and decoded.job_id == ARGV[2] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


# ---------------------------------------------------------------------------
# Atomic path lock operations
# ---------------------------------------------------------------------------

def _extract_path_from_lease_key(lease_key: str) -> str:
    """Extract the repo-relative path from a lease key.

    Key format: Workflow:<cluster>:lease:path>:<repo>:<path>
    Returns the <path> portion with '/' restored from lease-safe encoding.
    """
    # Split on ':' and take everything after 'lease:path:<repo>'
    parts = lease_key.split(":")
    # parts[0] = Workflow, [1] = cluster, [2] = lease, [3] = path, [4] = repo, [5:] = path segments
    if len(parts) < 6:
        return lease_key
    return "/".join(parts[5:])


def acquire_path_locks_atomic(
    r: redis.Redis,
    cluster: str,
    repo_slug: str,
    worker_id: str,
    job_id: str,
    paths: list[str],
    ttl: int = 300,
) -> tuple[bool, list[dict]]:
    """
    All-or-nothing path lock acquisition via Redis Lua.
    If any path fails, all acquired paths in this call are rolled back.
    Returns (success, list_of_blocked_details).
    """
    normalized = sorted({
        normalize_path_for_lease(p) for p in paths
        if normalize_path_for_lease(p)
    })
    if not normalized:
        return True, []

    keys = [lease_path(cluster, repo_slug, p) for p in normalized]
    now = _now()
    args = [
        json.dumps({
            "worker_id": worker_id,
            "job_id": job_id,
            "path": p,
            "lease_type": "path_write",
            "created_at": now,
        })
        for p in normalized
    ]
    args.append(str(ttl))

    result = r.eval(ACQUIRE_PATH_LOCKS_LUA, len(keys), *(keys + args))
    # result: [1, "", ""] on success, [0, blocking_key, existing_value] on failure
    if result[0] == 1:
        return True, []

    existing = json.loads(result[2]) if result[2] else {}
    return False, [{
        "path": _extract_path_from_lease_key(result[1]),
        "owner_worker_id": existing.get("worker_id"),
        "owner_job_id": existing.get("job_id"),
    }]


def renew_path_locks(
    r: redis.Redis,
    keys: list[str],
    worker_id: str,
    job_id: str,
    ttl: int,
) -> list[str]:
    """
    Renew TTL only on locks owned by this worker/job.
    Returns list of keys that failed renewal (not owned or expired).
    """
    failed = []
    for k in keys:
        result = r.eval(RENEW_LOCK_LUA, 1, k, worker_id, job_id, str(ttl))
        if result == 0:
            failed.append(k)
    return failed


def release_path_locks(
    r: redis.Redis,
    keys: list[str],
    worker_id: str,
    job_id: str,
) -> list[str]:
    """
    Release only locks owned by this worker/job.
    Returns list of keys that failed release (not owned).
    """
    failed = []
    for k in keys:
        result = r.eval(RELEASE_LOCK_LUA, 1, k, worker_id, job_id)
        if result == 0:
            failed.append(k)
    return failed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
