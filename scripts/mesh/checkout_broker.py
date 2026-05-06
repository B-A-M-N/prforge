#!/usr/bin/env python3
"""PRForge Checkout Broker — worktree lifecycle for distributed-local mode.

Manages isolated worktrees so multiple workers on the same machine never
touch the same checkout. Each worker-job gets its own worktree + branch.

Usage:
  python checkout_broker.py create --repo-url URL --repo-key OWNER/REPO \\
      --job-id JOB --worker-id WORKER --base-ref REF --target-number N --task-slug SLUG

  python checkout_broker.py list
  python checkout_broker.py cleanup --job-id JOB
  python checkout_broker.py quarantine --job-id JOB
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

MESH_HOME = Path.home() / ".prforge-mesh"
CONFIG_PATH = Path.home() / ".prforge-mesh" / "config.json"
CHECKOUTS_DIR = MESH_HOME / "checkouts"
REPO_CACHE_ROOT = Path.home() / ".prforge" / "repos"
WORKTREE_ROOT = Path.home() / ".prforge" / "worktrees"
QUARANTINE_ROOT = Path.home() / ".prforge" / "quarantine"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


def repo_slug(repo_key: str) -> str:
    """Normalize repo key to filesystem-safe slug."""
    return repo_key.replace("/", "__")


def bare_repo_path(repo_key: str) -> Path:
    return REPO_CACHE_ROOT / f"{repo_slug(repo_key)}.git"


def worktree_path(repo_key: str, job_id: str) -> Path:
    return WORKTREE_ROOT / repo_slug(repo_key) / job_id


def checkout_meta_path(job_id: str) -> Path:
    return CHECKOUTS_DIR / f"{job_id}.json"


def run(cmd: list[str], cwd: Optional[Path] = None, check: bool = True) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd) if cwd else None)
    if check and result.returncode != 0:
        print(f"ERROR: {' '.join(cmd)}\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def ensure_bare_cache(repo_url: str, repo_key: str) -> Path:
    """Clone or fetch bare repo cache."""
    bare = bare_repo_path(repo_key)
    if not bare.exists():
        print(f"Cloning bare cache: {repo_url}")
        REPO_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--bare", repo_url, str(bare)])
    else:
        print(f"Fetching updates for {repo_key}")
        run(["git", "fetch", "--all"], cwd=bare)
    return bare


def generate_branch_name(target_number: int, task_slug: str, job_id: str) -> str:
    """Generate unique branch name: prforge/<number>-<slug>-<short-job-id>"""
    short_job = job_id[-6:] if len(job_id) > 6 else job_id
    # Sanitize slug: lowercase, alphanumeric + hyphens only
    slug = re.sub(r"[^a-z0-9-]", "-", task_slug.lower()).strip("-")
    return f"prforge/{target_number}-{slug}-{short_job}"


def create_checkout(
    repo_url: str,
    repo_key: str,
    job_id: str,
    worker_id: str,
    base_ref: str,
    target_number: int,
    task_slug: str,
) -> dict:
    """Create isolated worktree for a job. Returns checkout metadata."""
    # Ensure directories exist
    CHECKOUTS_DIR.mkdir(parents=True, exist_ok=True)
    WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)

    # Check for existing checkout
    meta_path = checkout_meta_path(job_id)
    if meta_path.exists():
        existing = json.loads(meta_path.read_text())
        if existing.get("state") == "active":
            print(f"Checkout already exists for {job_id}: {existing['worktree']}")
            return existing

    # Ensure bare cache
    bare = ensure_bare_cache(repo_url, repo_key)

    # Generate branch
    branch = generate_branch_name(target_number, task_slug, job_id)
    wt_path = worktree_path(repo_key, job_id)

    # Create worktree
    if wt_path.exists():
        print(f"Worktree path exists, removing: {wt_path}")
        shutil.rmtree(wt_path)

    print(f"Creating worktree: {wt_path} (branch: {branch})")
    run(["git", "worktree", "add", str(wt_path), "-b", branch, base_ref], cwd=bare)

    # Write checkout metadata
    meta = {
        "job_id": job_id,
        "worker_id": worker_id,
        "repo_key": repo_key,
        "repo_slug": repo_slug(repo_key),
        "repo_url": repo_url,
        "bare_cache": str(bare),
        "worktree": str(wt_path),
        "base_ref": base_ref,
        "branch": branch,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "state": "active",
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    # Write checkout.json inside worktree for hook verification
    checkout_json = wt_path / ".prforge" / "checkout.json"
    checkout_json.parent.mkdir(parents=True, exist_ok=True)
    checkout_json.write_text(json.dumps(meta, indent=2) + "\n")

    print(f"Checkout created: {wt_path}")
    return meta


def list_checkouts() -> list[dict]:
    """List all active checkouts."""
    if not CHECKOUTS_DIR.exists():
        return []
    checkouts = []
    for f in CHECKOUTS_DIR.glob("*.json"):
        try:
            meta = json.loads(f.read_text())
            checkouts.append(meta)
        except (json.JSONDecodeError, OSError):
            continue
    return checkouts


def cleanup_checkout(job_id: str) -> bool:
    """Remove worktree and delete branch. Returns True if successful."""
    meta_path = checkout_meta_path(job_id)
    if not meta_path.exists():
        print(f"No checkout found for {job_id}")
        return False

    meta = json.loads(meta_path.read_text())
    wt_path = Path(meta["worktree"])
    bare = Path(meta["bare_cache"])
    branch = meta["branch"]

    # Remove worktree
    if wt_path.exists():
        print(f"Removing worktree: {wt_path}")
        try:
            run(["git", "worktree", "remove", "--force", str(wt_path)], cwd=bare)
        except SystemExit:
            # Worktree might already be gone
            if wt_path.exists():
                shutil.rmtree(wt_path)

    # Delete branch
    if bare.exists():
        print(f"Deleting branch: {branch}")
        try:
            run(["git", "branch", "-D", branch], cwd=bare)
        except SystemExit:
            pass  # Branch might already be gone

    # Update metadata
    meta["state"] = "cleaned"
    meta["cleaned_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    print(f"Checkout cleaned: {job_id}")
    return True


def quarantine_checkout(job_id: str) -> bool:
    """Move dirty/stale worktree to quarantine. Preserves work for recovery."""
    meta_path = checkout_meta_path(job_id)
    if not meta_path.exists():
        print(f"No checkout found for {job_id}")
        return False

    meta = json.loads(meta_path.read_text())
    wt_path = Path(meta["worktree"])

    if not wt_path.exists():
        print(f"Worktree already gone: {wt_path}")
        meta["state"] = "quarantined"
        meta["quarantined_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")
        return True

    # Move to quarantine
    quarantine_path = QUARANTINE_ROOT / job_id
    QUARANTINE_ROOT.mkdir(parents=True, exist_ok=True)

    if quarantine_path.exists():
        shutil.rmtree(quarantine_path)

    print(f"Quarantining: {wt_path} → {quarantine_path}")
    shutil.move(str(wt_path), str(quarantine_path))

    # Update metadata
    meta["state"] = "quarantined"
    meta["quarantine_path"] = str(quarantine_path)
    meta["quarantined_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    print(f"Quarantined: {job_id}")
    return True


def cmd_create(args):
    meta = create_checkout(
        repo_url=args.repo_url,
        repo_key=args.repo_key,
        job_id=args.job_id,
        worker_id=args.worker_id,
        base_ref=args.base_ref,
        target_number=args.target_number,
        task_slug=args.task_slug,
    )
    print(json.dumps(meta, indent=2))


def cmd_list(args):
    checkouts = list_checkouts()
    if not checkouts:
        print("No active checkouts.")
        return
    for c in checkouts:
        print(f"{c['job_id']}  {c['state']:10s}  {c['worker_id']:12s}  {c['worktree']}")


def cmd_cleanup(args):
    cleanup_checkout(args.job_id)


def cmd_quarantine(args):
    quarantine_checkout(args.job_id)


def cmd_status(args):
    """Show full status including leases (requires Redis)."""
    checkouts = list_checkouts()
    config = load_config()

    print("PRForge Checkout Broker Status")
    print(f"  Mode: {config.get('mode', 'none')}")
    print(f"  Worktree root: {WORKTREE_ROOT}")
    print(f"  Repo cache: {REPO_CACHE_ROOT}")
    print(f"  Active checkouts: {len(checkouts)}")

    if checkouts:
        print()
        for c in checkouts:
            print(f"  {c['job_id']}")
            print(f"    worker:   {c['worker_id']}")
            print(f"    repo:     {c['repo_key']}")
            print(f"    branch:   {c['branch']}")
            print(f"    worktree: {c['worktree']}")
            print(f"    state:    {c['state']}")

    # Show Redis leases if available
    try:
        from redis_backend import connect, list_all_leases, key
        redis_url = config.get("redis", {}).get("url", os.environ.get("PRFORGE_MESH_REDIS", ""))
        if redis_url:
            r = connect(redis_url)
            cluster = config.get("cluster", "default")
            leases = list_all_leases(r, cluster)
            if leases:
                print(f"\n  Active leases: {len(leases)}")
                for lk in leases[:20]:
                    print(f"    {lk.get('worker_id', '?'):12s}  {lk.get('job_id', '?'):12s}  {lk.get('key', '?')}")
    except Exception:
        pass  # Redis not available, skip


def main():
    parser = argparse.ArgumentParser(description="PRForge Checkout Broker")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("create", help="Create isolated worktree for a job")
    p.add_argument("--repo-url", required=True)
    p.add_argument("--repo-key", required=True, help="owner/repo")
    p.add_argument("--job-id", required=True)
    p.add_argument("--worker-id", required=True)
    p.add_argument("--base-ref", default="origin/main")
    p.add_argument("--target-number", type=int, default=0)
    p.add_argument("--task-slug", default="fix")

    sub.add_parser("list", help="List active checkouts")

    p = sub.add_parser("cleanup", help="Remove worktree and branch")
    p.add_argument("--job-id", required=True)

    p = sub.add_parser("quarantine", help="Move dirty worktree to quarantine")
    p.add_argument("--job-id", required=True)

    sub.add_parser("status", help="Show full status")

    args = parser.parse_args()

    if args.command == "create":
        cmd_create(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "cleanup":
        cmd_cleanup(args)
    elif args.command == "quarantine":
        cmd_quarantine(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
