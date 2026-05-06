#!/usr/bin/env python3
"""PRForge Mesh Lock Guard — verifies distributed-local lock state before tool use.

Called by hooks/mesh-lock-guard.sh as a PreToolUse hook. Checks:
1. Is mesh mode active?
2. Is this session a worker?
3. Is current cwd inside assigned worktree?
4. Do job/target/branch leases exist and belong to this worker?
5. After PLAN, do path leases cover the target file?
6. For Bash, block write/destructive operations outside worktree.

Exits 0 (allow) or 1 (block with redirect message).
"""

import json
import os
import sys
from pathlib import Path

# Add parent dir to path for redis_backend import
sys.path.insert(0, str(Path(__file__).parent))
from redis_backend import (
    connect, get_lease, normalize_path_for_lease,
)

MESH_CONFIG = Path.home() / ".prforge-mesh" / "config.json"
CHECKOUT_META_DIR = Path.home() / ".prforge-mesh" / "checkouts"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def get_assigned_checkout(worker_id: str) -> dict:
    """Find the active checkout assigned to this worker."""
    if not CHECKOUT_META_DIR.exists():
        return {}
    for f in CHECKOUT_META_DIR.glob("*.json"):
        meta = load_json(f)
        if meta.get("worker_id") == worker_id and meta.get("state") == "active":
            return meta
    return {}


def check_lease(redis_conn, key: str, expected_worker: str) -> tuple[bool, str]:
    """Check if lease exists and belongs to expected worker. Returns (ok, message)."""
    val = get_lease(redis_conn, key)
    if val is None:
        return False, f"Lease not found: {key}"
    if val.get("worker_id") != expected_worker:
        return False, f"Lease held by {val.get('worker_id', '?')}: {key}"
    return True, ""


def normalize_path_for_lease(path: str) -> str:
    """Normalize a file path for lease key matching."""
    # Convert to repo-relative POSIX path
    p = path.replace("\\", "/").lstrip("/")
    # Reject absolute paths and path traversal
    if ".." in p or p.startswith("/"):
        return ""
    return p


def main():
    # Read hook input from stdin (Claude Code passes tool input as JSON)
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        hook_input = {}

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    # Only enforce for Bash, Write, Edit, MultiEdit
    if tool_name not in ("Bash", "Write", "Edit", "MultiEdit"):
        sys.exit(0)

    # Check if mesh mode is active
    config = load_json(MESH_CONFIG)
    if not config or config.get("mode") not in ("local", "distributed"):
        sys.exit(0)

    # Check if this session is a worker
    worker_id = config.get("worker_id", "")
    if not worker_id:
        sys.exit(0)  # Not a worker session, skip

    # Get assigned checkout
    checkout = get_assigned_checkout(worker_id)
    if not checkout:
        # Worker has no active checkout — block writes
        print(f"⛔ PRFORGE MESH BLOCK: Worker {worker_id} has no active checkout.", file=sys.stderr)
        print("   Run /pr-distributed-local worker to register and get a job.", file=sys.stderr)
        sys.exit(1)

    wt_path = Path(checkout["worktree"])
    job_id = checkout["job_id"]
    repo_key = checkout["repo_key"]
    branch = checkout["branch"]

    # Check cwd is inside worktree
    cwd = Path.cwd()
    try:
        cwd.relative_to(wt_path)
    except ValueError:
        # cwd is NOT inside worktree
        print(f"⛔ PRFORGE MESH BLOCK: Current directory is not the assigned worktree.", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"   Expected: {wt_path}", file=sys.stderr)
        print(f"   Current:  {cwd}", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"   Recovery: cd {wt_path}", file=sys.stderr)
        sys.exit(1)

    # Connect to Redis and verify leases
    redis_url = config.get("redis", {}).get("url", os.environ.get("PRFORGE_MESH_REDIS", ""))
    if not redis_url:
        sys.exit(0)  # Can't verify, allow

    try:
        r = connect(redis_url)
    except Exception:
        sys.exit(0)  # Redis unavailable, allow (don't block on infra failure)

    cluster = config.get("cluster", "default")

    # Check job lease
    from redis_backend import lease_job, lease_target, lease_branch
    ok, msg = check_lease(r, lease_job(cluster, job_id), worker_id)
    if not ok:
        print(f"⛔ PRFORGE MESH BLOCK: {msg}", file=sys.stderr)
        sys.exit(1)

    # Check target lease
    target_number = checkout.get("target_number", "")
    if target_number:
        ok, msg = check_lease(r, lease_target(cluster, repo_key.replace("/", "_"), "pr", str(target_number)), worker_id)
        if not ok:
            print(f"⛔ PRFORGE MESH BLOCK: {msg}", file=sys.stderr)
            sys.exit(1)

    # Check branch lease
    ok, msg = check_lease(r, lease_branch(cluster, repo_key.replace("/", "_"), branch), worker_id)
    if not ok:
        print(f"⛔ PRFORGE MESH BLOCK: {msg}", file=sys.stderr)
        sys.exit(1)

    # For Write/Edit/MultiEdit, check path is within scope
    if tool_name in ("Write", "Edit", "MultiEdit"):
        target_file = tool_input.get("file_path", "")
        if target_file:
            rel = normalize_path_for_lease(target_file)
            if rel:
                # Check scope.json
                scope_path = wt_path / ".prforge" / "scope.json"
                scope = load_json(scope_path)
                allowed = scope.get("allowed_paths", [])
                forbidden = scope.get("forbidden_paths", [])

                # Check forbidden
                for fp in forbidden:
                    if rel.startswith(fp) or fp in rel:
                        print(f"⛔ PRFORGE MESH BLOCK: Path is in forbidden scope: {rel}", file=sys.stderr)
                        sys.exit(1)

                # Check path leases (after PLAN)
                locks_path = wt_path / ".prforge" / "locks.json"
                locks = load_json(locks_path)
                if locks.get("leases", {}).get("paths"):
                    # Path leases exist — verify this path is covered
                    path_leases = locks["leases"]["paths"]
                    covered = any(
                        normalize_path_for_lease(pl.get("path", "")) in rel
                        or rel in normalize_path_for_lease(pl.get("path", ""))
                        for pl in path_leases
                    )
                    if not covered and allowed:
                        # Check if path is in allowed list
                        in_allowed = any(
                            rel.startswith(ap) or ap in rel
                            for ap in allowed
                        )
                        if not in_allowed:
                            print(f"⛔ PRFORGE MESH BLOCK: Path not in leased scope: {rel}", file=sys.stderr)
                            print(f"   Allowed paths: {allowed}", file=sys.stderr)
                            sys.exit(1)

    # For Bash, block dangerous operations outside worktree
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        # Allow known safe commands
        safe_prefixes = [
            "python3 scripts/mesh/",
            "redis-cli ping",
            "redis-cli info",
            "git status", "git log", "git show", "git diff", "git branch",
            "git remote", "git fetch", "git rev-parse",
            "pwd", "ls", "cat", "rg", "grep", "find",
            "echo", "cd", "mkdir", "touch",
            "npm test", "npm run", "npx",
            "pytest", "python -m",
        ]
        # Block dangerous operations
        dangerous_patterns = [
            "git push", "git commit", "git checkout", "git reset", "git clean",
            "rm -rf", "rm -f", "rm ",
            "mv ", "cp ",
            "sed -i", "tee ",
            "npm install", "npm ci", "pip install",
        ]

        is_safe = any(cmd.startswith(p) or cmd == p.strip() for p in safe_prefixes)
        is_dangerous = any(p in cmd for p in dangerous_patterns)

        if is_dangerous and not is_safe:
            print(f"⛔ PRFORGE MESH BLOCK: Potentially destructive Bash command blocked.", file=sys.stderr)
            print(f"   Command: {cmd}", file=sys.stderr)
            print(f"   This command may modify files outside the leased scope.", file=sys.stderr)
            sys.exit(1)

    # All checks passed
    sys.exit(0)


if __name__ == "__main__":
    main()
