"""
PRForge Mesh — worker loop.
Polls Redis for assigned jobs, writes outside-repo inbox/job.json,
renews leases, and reports phase status back.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure sibling modules are importable when run from systemd
sys.path.insert(0, str(Path(__file__).parent))

import redis

from redis_backend import (
    emit_event,
    get_job,
    heartbeat,
    release_job_leases,
    release_path_locks,
    renew_job_leases,
    renew_path_locks,
    upsert_job,
)
from notifications import notify

logger = logging.getLogger("prforge.worker")


def _repo_slug(repo: str) -> str:
    return repo.replace("/", "__").replace(":", "_")


def _run_key(job: dict) -> str:
    pr_number = job.get("pr_number")
    if pr_number:
        return f"pr-{pr_number}"
    branch = job.get("head_branch", "") or "unknown-branch"
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in branch)


def _artifact_dir(job: dict, repo_path: str) -> Path:
    pointer = Path(repo_path) / ".prforge-run"
    if pointer.exists() and not pointer.is_symlink():
        data = {}
        for line in pointer.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
        if data.get("artifact_dir"):
            return Path(data["artifact_dir"])

    run_id = job.get("job_id") or datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")
    prforge_home = Path(os.environ.get("PRFORGE_HOME", str(Path.home() / ".prforge")))
    return prforge_home / "runs" / _repo_slug(job["repo"]) / _run_key(job) / run_id


def _ensure_pointer(job: dict, repo_path: str, artifact_dir: Path) -> None:
    pointer = Path(repo_path) / ".prforge-run"
    legacy_dir = Path(repo_path) / ".prforge"
    if pointer.is_symlink() or legacy_dir.is_symlink():
        raise RuntimeError("PRForge repo-local state must be a plain pointer file, not a symlink")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    pointer.write_text(
        "\n".join([
            f"run_id={artifact_dir.name}",
            f"artifact_dir={artifact_dir}",
            f"mesh_job_id={job.get('job_id', '')}",
            "",
        ])
    )

    git_dir = Path(repo_path) / ".git"
    if git_dir.exists():
        exclude = git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text().splitlines() if exclude.exists() else []
        with exclude.open("a") as f:
            for pat in (".prforge/", ".prforge-run", ".prforge-*"):
                if pat not in existing:
                    f.write(pat + "\n")


def _write_policy_bundle(job: dict, repo_path: str, artifact_dir: Path, config: dict) -> None:
    from datetime import timedelta

    constraints_raw = job.get("constraints", "{}")
    if isinstance(constraints_raw, str):
        try:
            constraints = json.loads(constraints_raw)
        except json.JSONDecodeError:
            constraints = {}
    else:
        constraints = constraints_raw if isinstance(constraints_raw, dict) else {}

    allowed_files = constraints.get("allowed_files", [])
    intel_context = artifact_dir / "intel_context.md"
    intel_hash = ""
    if intel_context.exists():
        import hashlib
        intel_hash = hashlib.sha256(intel_context.read_bytes()).hexdigest()

    lease_ttl = config.get("limits", {}).get("lease_ttl_seconds", 1800)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_ttl)
    bundle = {
        "policy_version": datetime.now(timezone.utc).isoformat(),
        "job_id": job["job_id"],
        "phase": "INTAKE",
        "allowed_files": allowed_files,
        "forbidden_actions": [
            "push",
            "force_push",
            "merge",
            "delete_branch",
            "gh_comment_without_approval",
        ],
        "allowed_local_actions": [
            "read",
            "search",
            "edit_approved_files",
            "run_tests",
            "update_notes",
            "resolve_local_redirects",
        ],
        "requires_mesh_reconnect": [
            "phase_exit",
            "contract_expansion",
            "approval_ready",
            "commit",
            "push",
            "gh_comment",
            "gh_pr_create",
            "queue_mutation",
            "lease_release",
            "completion",
        ],
        "risk_rules_hash": "builtin:v1",
        "intel_context_hash": intel_hash,
        "expires_at": expires_at.isoformat(),
    }
    (artifact_dir / "policy_bundle.json").write_text(json.dumps(bundle, indent=2))


def run(r: redis.Redis, cluster: str, config: dict) -> None:
    node_cfg  = config["mesh"]
    worker_cfg = config.get("worker", {})
    limits    = config.get("limits", {})
    notif_cfg = config.get("notifications", {})

    node_id   = node_cfg["node_id"]
    lease_ttl = limits.get("lease_ttl_seconds", 1800)
    hb_interval = limits.get("heartbeat_interval_seconds", 15)
    repo_roots  = worker_cfg.get("repo_roots", [])

    node_state: dict = {
        "node_id":    node_id,
        "roles":      "worker",
        "status":     "idle",
        "capacity":   worker_cfg.get("capacity", 1),
        "active_job": "",
        "repo_roots": repo_roots,
    }

    desktop = notif_cfg.get("desktop", True)
    pubsub  = notif_cfg.get("pubsub", True)

    logger.info("Worker started node=%s cluster=%s", node_id, cluster)

    while True:
        try:
            _tick(r, cluster, node_id, node_state, repo_roots,
                  lease_ttl, hb_interval, desktop, pubsub, config)
        except Exception as e:
            logger.exception("Worker tick error: %s", e)
        time.sleep(hb_interval)


def _tick(
    r: redis.Redis,
    cluster: str,
    node_id: str,
    node_state: dict,
    repo_roots: list,
    lease_ttl: int,
    hb_interval: int,
    desktop: bool,
    pubsub: bool,
    config: dict | None = None,
) -> None:
    # 1. Heartbeat
    heartbeat(r, cluster, node_state, ttl=hb_interval * 3)

    # 2. Check assigned job
    node_data = r.hgetall(f"Workflow:{cluster}:node:{node_id}")
    if not node_data:
        return

    active_job_id = node_data.get("active_job", "")
    if not active_job_id:
        node_state["status"] = "idle"
        node_state["active_job"] = ""
        return

    job = get_job(r, cluster, active_job_id)
    if not job:
        logger.warning("Active job %s not found in Redis", active_job_id)
        _clear_job(r, cluster, node_id, node_state)
        return

    status = job.get("status", "")

    # 3. Newly assigned job — write inbox
    if status == "assigned":
        repo_path = _resolve_repo(job["repo"], repo_roots)
        if repo_path is None:
            logger.error("Cannot find repo %s in roots %s", job["repo"], repo_roots)
            upsert_job(r, cluster, {**job, "status": "blocked"})
            notify(r, cluster, "WorkerBlocked",
                   f"Repo {job['repo']} not found on {node_id}",
                   desktop=desktop, pubsub=pubsub)
            return

        _write_inbox(job, repo_path, cluster, node_id, config or {"limits": {"lease_ttl_seconds": lease_ttl}})
        _write_distributed_json(job, repo_path, cluster, node_id, config)

        upsert_job(r, cluster, {**job, "status": "active"})
        node_state["status"]     = "active"
        node_state["active_job"] = active_job_id

        emit_event(r, cluster, "JobDispatched", {
            "job_id":    active_job_id,
            "node":      node_id,
            "repo":      job["repo"],
            "pr_number": str(job.get("pr_number", "")),
        })
        artifact_dir = _artifact_dir(job, repo_path)
        notify(r, cluster, "JobDispatched",
               f"Job {active_job_id} ready at {artifact_dir}/inbox/job.json",
               desktop=desktop, pubsub=pubsub)
        logger.info("Wrote inbox for job=%s repo=%s", active_job_id, repo_path)

    # 4. Active job — renew leases, read outbox status
    elif status == "active":
        failed_leases = renew_job_leases(
            r, cluster,
            active_job_id,
            job["repo"],
            str(job.get("pr_number", "")),
            job.get("head_branch", ""),
            node_id,
            lease_ttl,
        )
        path_keys = _parse_json_list(job.get("path_keys", "[]"))
        if path_keys:
            failed_leases.extend(renew_path_locks(r, path_keys, node_id, active_job_id, lease_ttl))
        if failed_leases:
            logger.error("Lease renewal failed job=%s failed=%s", active_job_id, failed_leases)
            upsert_job(r, cluster, {
                **job,
                "status": "blocked",
                "blocker": "lease_renewal_failed",
                "failed_leases": json.dumps(failed_leases),
            })
            node_state["status"] = "blocked"
            emit_event(r, cluster, "LeaseRenewalFailed", {
                "job_id": active_job_id,
                "node": node_id,
                "failed_leases": json.dumps(failed_leases),
            })
            notify(r, cluster, "LeaseRenewalFailed",
                   f"Job {active_job_id} blocked because leases expired or changed owner",
                   desktop=desktop, pubsub=pubsub)
            return
        repo_path = _resolve_repo(job["repo"], repo_roots)
        if repo_path:
            _read_outbox_status(r, cluster, active_job_id, job, repo_path,
                                node_id, node_state, desktop, pubsub)

    # 5. Terminal states — release leases, clear node
    elif status in ("complete", "failed", "approval_ready", "blocked"):
        _finalize(r, cluster, job, node_id, node_state, desktop, pubsub)


def _write_inbox(job: dict, repo_path: str, cluster: str, node_id: str, config: dict) -> None:
    artifact_dir = _artifact_dir(job, repo_path)
    _ensure_pointer(job, repo_path, artifact_dir)
    _write_policy_bundle(job, repo_path, artifact_dir, config)
    inbox_dir = artifact_dir / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    constraints_raw = job.get("constraints", "{}")
    if isinstance(constraints_raw, str):
        try:
            constraints = json.loads(constraints_raw)
        except json.JSONDecodeError:
            constraints = {}
    else:
        constraints = constraints_raw

    def _parse_list_field(val, default: list) -> list:
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, list) else default
            except (json.JSONDecodeError, ValueError):
                return default
        return default

    packet = {
        "mesh": {
            "enabled":      True,
            "cluster_name": cluster,
            "node_id":      node_id,
            "role":         "worker",
        },
        "job": {
            "job_id":               job["job_id"],
            "lease_id":             job.get("lease_id", ""),
            "type":                 job["type"],
            "priority":             job.get("priority", "P4"),
            "repo":                 job["repo"],
            "pr_number":            int(job.get("pr_number", 0)),
            "base_branch":          job.get("base_branch", "main"),
            "head_branch":          job.get("head_branch", ""),
            "source_url":           job.get("source_url", ""),
            # Acceptance criteria passed through from job enqueue so the
            # auditor can verify against the original requirements, not just
            # the worker's self-attested dod.md.
            "original_objective":   job.get("objective", job.get("source_url", "")),
            "acceptance_criteria":  _parse_list_field(job.get("acceptance_criteria"), []),
            "dod_requirements":     _parse_list_field(job.get("dod_requirements"), []),
        },
        "constraints": {
            "public_actions_require_approval":   True,
            "only_address_main_review_feedback": job["type"] == "review_response",
            "ignore_unrelated_ci":               True,
            "do_not_create_new_pr":              job["type"] != "new_pr",
            **constraints,
        },
    }

    inbox_path = inbox_dir / "job.json"
    inbox_path.write_text(json.dumps(packet, indent=2))
    _write_locks_json(job, artifact_dir)


def _parse_json_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _write_locks_json(job: dict, artifact_dir: Path) -> None:
    """Write the local lock manifest consumed by mesh-lock-guard."""
    path_keys = _parse_json_list(job.get("path_keys", "[]"))
    write_set = _parse_json_list(job.get("write_set", "[]"))
    paths = []
    if write_set:
        paths = [{"path": p, "lease_key": path_keys[i] if i < len(path_keys) else ""} for i, p in enumerate(write_set)]
    elif path_keys:
        paths = [{"path": k.split(":")[-1], "lease_key": k} for k in path_keys]
    locks = {
        "job_id": job.get("job_id", ""),
        "leases": {
            "paths": paths,
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (artifact_dir / "locks.json").write_text(json.dumps(locks, indent=2) + "\n")


def _write_submission_json(job: dict, repo_path: str, cluster: str, node_id: str) -> None:
    """Write outbox/submission.json when manager mode is active."""
    from datetime import datetime, timezone as tz
    artifact_dir = _artifact_dir(job, repo_path)
    _ensure_pointer(job, repo_path, artifact_dir)
    outbox_dir = artifact_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)

    # Gather artifact fingerprints
    pf_dir = artifact_dir
    artifacts = {}
    for artifact_name in ("approval.md", "validation_ledger.md", "dod.md", "hostile_review.md"):
        ap = pf_dir / artifact_name
        if ap.exists():
            import hashlib
            artifacts[artifact_name] = hashlib.sha256(ap.read_bytes()).hexdigest()

    # Current diff hash
    import subprocess
    diff_hash = ""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat"],
            capture_output=True, text=True, timeout=15,
            cwd=repo_path,
        )
        if result.returncode == 0:
            import hashlib
            staged = subprocess.run(
                ["git", "diff", "--cached", "--stat"],
                capture_output=True, text=True, timeout=15,
                cwd=repo_path,
            )
            diff_hash = hashlib.sha256(
                (result.stdout + staged.stdout).encode()
            ).hexdigest()
    except Exception:
        pass

    submission = {
        "job_id":      job["job_id"],
        "node_id":     node_id,
        "cluster":     cluster,
        "repo":        job["repo"],
        "pr_number":   job.get("pr_number"),
        "head_branch": job.get("head_branch", ""),
        "base_branch": job.get("base_branch", "main"),
        "submitted_at": datetime.now(tz.utc).isoformat(),
        "artifacts":   artifacts,
        "diff_hash":   diff_hash,
        "status":      "WorkerSubmissionReady",
    }

    (outbox_dir / "submission.json").write_text(json.dumps(submission, indent=2))


def _write_distributed_json(
    job: dict,
    repo_path: str,
    cluster: str,
    node_id: str,
    config: dict | None = None,
) -> None:
    pf_dir = _artifact_dir(job, repo_path)
    _ensure_pointer(job, repo_path, pf_dir)
    pf_dir.mkdir(parents=True, exist_ok=True)
    mgr_cfg   = (config or {}).get("manager_mode", {})
    lease_ttl = (config or {}).get("limits", {}).get("lease_ttl_seconds", 1800)
    data = {
        "mesh_enabled":       True,
        "cluster":            cluster,
        "node_id":            node_id,
        "roles":              (config or {}).get("mesh", {}).get("roles", ["worker"]),
        "job_id":             job["job_id"],
        "job_type":           job["type"],
        "pr_number":          job.get("pr_number"),
        "repo":               job["repo"],
        "assigned_at":        datetime.now(timezone.utc).isoformat(),
        "lease_ttl_seconds":  lease_ttl,
        # manager_mode must be propagated so _read_outbox_status can trigger
        # WorkerSubmissionReady when the worker reaches approval_ready.
        "manager_mode": {
            "enabled":   mgr_cfg.get("enabled", False),
            "authority": mgr_cfg.get("authority", "off"),
        },
    }
    (pf_dir / "distributed.json").write_text(json.dumps(data, indent=2))


def _read_outbox_status(
    r: redis.Redis,
    cluster: str,
    job_id: str,
    job: dict,
    repo_path: str,
    node_id: str,
    node_state: dict,
    desktop: bool,
    pubsub: bool,
) -> None:
    artifact_dir = _artifact_dir(job, repo_path)
    status_path = artifact_dir / "outbox" / "status.json"
    if not status_path.exists():
        return
    try:
        status_data = json.loads(status_path.read_text())
    except json.JSONDecodeError:
        return

    status = status_data.get("status", "")

    if status == "approval_ready":
        # Check if manager mode is active — if so, write submission.json
        # and report WorkerSubmissionReady instead of requesting approval directly
        distributed_path = artifact_dir / "distributed.json"
        if distributed_path.exists():
            try:
                distributed_cfg = json.loads(distributed_path.read_text())
            except (json.JSONDecodeError, OSError):
                distributed_cfg = {}
            manager_cfg = distributed_cfg.get("manager_mode", {})
            if manager_cfg.get("enabled", False):
                _write_submission_json(job, repo_path, cluster, node_id)
                upsert_job(r, cluster, {**job, "status": "approval_ready"})
                node_state["status"] = "active"
                emit_event(r, cluster, "WorkerSubmissionReady", {
                    "job_id":    job_id,
                    "node":      node_id,
                    "repo":      job["repo"],
                    "pr_number": str(job.get("pr_number", "")),
                })
                notify(r, cluster, "WorkerSubmissionReady",
                       f"Job {job_id} submission ready for manager review on {node_id}",
                       desktop=desktop, pubsub=pubsub)
                return  # Do not fall through to normal approval_ready handling

        upsert_job(r, cluster, {**job, "status": "approval_ready"})
        node_state["status"] = "active"
        emit_event(r, cluster, "ApprovalReady", {
            "job_id":    job_id,
            "node":      node_id,
            "repo":      job["repo"],
            "pr_number": str(job.get("pr_number", "")),
        })
        notify(r, cluster, "ApprovalReady",
               f"Job {job_id} needs /pr-approve on {node_id}",
               desktop=desktop, pubsub=pubsub)

    elif status in ("complete", "failed", "blocked"):
        upsert_job(r, cluster, {**job, "status": status})


def _finalize(
    r: redis.Redis,
    cluster: str,
    job: dict,
    node_id: str,
    node_state: dict,
    desktop: bool,
    pubsub: bool,
) -> None:
    release_job_leases(
        r, cluster,
        job["job_id"],
        job["repo"],
        str(job.get("pr_number", "")),
        job.get("head_branch", ""),
        node_id,
    )
    path_keys = _parse_json_list(job.get("path_keys", "[]"))
    if path_keys:
        release_path_locks(r, path_keys, node_id, job["job_id"])
    _clear_job(r, cluster, node_id, node_state)
    if job.get("status") == "approval_ready":
        notify(r, cluster, "ApprovalReady",
               f"Job {job['job_id']} ready for /pr-approve",
               desktop=desktop, pubsub=pubsub)


def _clear_job(r: redis.Redis, cluster: str, node_id: str, node_state: dict) -> None:
    r.hset(f"Workflow:{cluster}:node:{node_id}", mapping={
        "status":     "idle",
        "active_job": "",
    })
    node_state["status"]     = "idle"
    node_state["active_job"] = ""


def _resolve_repo(repo_slug: str, repo_roots: list) -> Optional[str]:
    """Find local path for a given org/repo slug under configured roots."""
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
