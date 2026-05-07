#!/usr/bin/env python3
"""PRForge Mesh Lock Guard — verifies distributed lock state before tool use.

Called by hooks/mesh-lock-guard.sh as a PreToolUse hook. Checks:
1. Config resolved and worker identity verified
2. Is mesh mode active?
3. Is this session a worker?
4. Is current cwd inside assigned worktree?
5. Do job/target/branch leases exist and belong to this worker?
6. Phase-aware write restrictions (PLAN vs IMPLEMENT)
7. After PLAN, do path leases cover the target file?
8. For Bash, block write/destructive operations outside worktree.

Exits 0 (allow) or 1 (block with redirect message).
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add parent dir to path for redis_backend import
sys.path.insert(0, str(Path(__file__).parent))
try:
    from redis_backend import (
        connect, get_lease, normalize_path_for_lease,
        lease_job, lease_target, lease_branch, lease_path,
    )
    REDIS_IMPORT_ERROR = None
except Exception as exc:
    REDIS_IMPORT_ERROR = exc

    def normalize_path_for_lease(path: str) -> str:
        p = path.replace("\\", "/").lstrip("/")
        if ".." in p or p.startswith("/"):
            return ""
        return p

CHECKOUT_META_DIR = Path.home() / ".prforge-mesh" / "checkouts"

# Phase that allows free planning without path locks
PLAN_PHASES = {"INTAKE", "DISCOVER", "TRIAGE", "PLAN"}

# During PLAN, only these path prefixes may be written
PLAN_ALLOWED_WRITE_PREFIXES = [
    ".prforge/scope.json",
    ".prforge/patch_plan.md",
    ".prforge/contract.md",
    ".prforge/state.json",
    ".prforge/inbox/",
    ".prforge/outbox/",
    ".prforge/advisory/",
]


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def artifact_dir_for_worktree(wt_path: Path) -> Path:
    pointer = wt_path / ".prforge-run"
    if pointer.exists() and not pointer.is_symlink():
        try:
            for line in pointer.read_text().splitlines():
                if line.startswith("artifact_dir="):
                    return Path(line.split("=", 1)[1]).expanduser()
        except OSError:
            pass
    return wt_path / ".prforge"


def repo_relative_path(file_path: str, wt_path: Path) -> str:
    if not file_path:
        return ""
    p = Path(file_path)
    try:
        if p.is_absolute():
            return normalize_path_for_lease(str(p.resolve().relative_to(wt_path.resolve())))
    except ValueError:
        return ""
    return normalize_path_for_lease(file_path)


def normalized_config(config: dict) -> dict:
    """Accept both legacy flat config and meshctl's nested config schema."""
    mesh = config.get("mesh") if isinstance(config.get("mesh"), dict) else {}
    redis_cfg = config.get("redis") if isinstance(config.get("redis"), dict) else {}

    mode = config.get("mode") or os.environ.get("PRFORGE_MESH_MODE", "")
    if not mode:
        cfg_env = os.environ.get("PRFORGE_MESH_CONFIG", "")
        if "/lan/" in cfg_env:
            mode = "lan"
        elif "/local/" in cfg_env or cfg_env.endswith("/config.json"):
            mode = "local"

    redis_url = (
        redis_cfg.get("url")
        or mesh.get("redis_url")
        or config.get("redis_url")
        or os.environ.get("PRFORGE_MESH_REDIS", "")
    )

    return {
        "mode": mode,
        "worker_id": config.get("worker_id") or mesh.get("node_id", ""),
        "redis_url": redis_url,
        "cluster": config.get("cluster") or mesh.get("cluster_name") or "default",
    }


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


def is_plan_phase(wt_path: Path) -> bool:
    """Check if the current phase is a planning phase."""
    state_path = artifact_dir_for_worktree(wt_path) / "state.json"
    state = load_json(state_path)
    return state.get("phase", "") in PLAN_PHASES


def is_plan_allowed_write(file_path: str) -> bool:
    """During PLAN, only .prforge metadata files may be written."""
    rel = normalize_path_for_lease(file_path)
    if not rel:
        return False
    return any(rel.startswith(prefix) for prefix in PLAN_ALLOWED_WRITE_PREFIXES)


def path_is_covered_by_lease(rel_path: str, path_leases: list[dict]) -> bool:
    """Return true only when rel_path is exactly leased or under a leased directory."""
    rel = normalize_path_for_lease(rel_path)
    if not rel:
        return False
    for lease in path_leases:
        leased = normalize_path_for_lease(str(lease.get("path", ""))).rstrip("/")
        if not leased:
            continue
        if rel == leased or rel.startswith(f"{leased}/"):
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="PRForge Mesh Lock Guard")
    parser.add_argument("--config", required=True, help="Path to mesh config JSON")
    parser.add_argument("--worker-id", required=True, help="Worker ID from env")
    args = parser.parse_args()

    config_path = Path(args.config)
    env_worker_id = args.worker_id

    # Read config
    config = load_json(config_path)
    if not config:
        print("⛔ PRFORGE MESH BLOCK: Config file is empty or unreadable.", file=sys.stderr)
        sys.exit(1)

    cfg = normalized_config(config)

    # Verify mode
    mode = cfg.get("mode", "")
    if mode not in ("local", "lan"):
        # Not a mesh worker config — allow
        sys.exit(0)

    # Verify worker_id in config matches env
    config_worker_id = cfg.get("worker_id", "")
    if not config_worker_id:
        print("⛔ PRFORGE MESH BLOCK: Config missing worker_id.", file=sys.stderr)
        sys.exit(1)
    if config_worker_id != env_worker_id:
        print(f"⛔ PRFORGE MESH BLOCK: Worker ID mismatch. "
              f"env={env_worker_id} config={config_worker_id}", file=sys.stderr)
        sys.exit(1)

    worker_id = config_worker_id

    # Read hook input from stdin
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        hook_input = {}

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    # Only enforce for Bash, Write, Edit, MultiEdit
    if tool_name not in ("Bash", "Write", "Edit", "MultiEdit"):
        sys.exit(0)

    # Get assigned checkout
    checkout = get_assigned_checkout(worker_id)
    if not checkout:
        print(f"⛔ PRFORGE MESH BLOCK: Worker {worker_id} has no active checkout.", file=sys.stderr)
        print("   Run /pr-distributed-local worker or /pr-distributed forge to register.", file=sys.stderr)
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
        print(f"⛔ PRFORGE MESH BLOCK: Current directory is not the assigned worktree.", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"   Expected: {wt_path}", file=sys.stderr)
        print(f"   Current:  {cwd}", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"   Recovery: cd {wt_path}", file=sys.stderr)
        sys.exit(1)

    # Phase-aware write restrictions
    artifact_dir = artifact_dir_for_worktree(wt_path)
    current_phase = load_json(artifact_dir / "state.json").get("phase", "")

    if current_phase in PLAN_PHASES and tool_name in ("Write", "Edit", "MultiEdit"):
        target_file = tool_input.get("file_path", "")
        rel_target = repo_relative_path(target_file, wt_path)
        if target_file and not is_plan_allowed_write(rel_target):
            print(f"⛔ PRFORGE MESH BLOCK: Phase is {current_phase} — "
                  f"source file edits not allowed during planning.", file=sys.stderr)
            print(f"   Blocked path: {target_file}", file=sys.stderr)
            print(f"   Allowed writes: .prforge/ metadata only", file=sys.stderr)
            print(f"   Complete PLAN and wait for coordinator IMPLEMENT certification.", file=sys.stderr)
            sys.exit(1)

    if REDIS_IMPORT_ERROR is not None:
        print(
            f"⛔ PRFORGE MESH BLOCK: Redis dependency unavailable: {REDIS_IMPORT_ERROR}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Connect to Redis and verify leases
    redis_url = cfg.get("redis_url", "")
    if not redis_url:
        print("⛔ PRFORGE MESH BLOCK: No Redis URL in config.", file=sys.stderr)
        sys.exit(1)

    try:
        r = connect(redis_url)
    except Exception:
        print("⛔ PRFORGE MESH BLOCK: Cannot connect to Redis — refusing unsafe write.", file=sys.stderr)
        sys.exit(1)

    cluster = cfg.get("cluster", "default")

    # Check job lease
    ok, msg = check_lease(r, lease_job(cluster, job_id), worker_id)
    if not ok:
        print(f"⛔ PRFORGE MESH BLOCK: {msg}", file=sys.stderr)
        sys.exit(1)

    # Check target lease
    target_number = checkout.get("target_number", "")
    if target_number:
        ok, msg = check_lease(
            r,
            lease_target(cluster, repo_key.replace("/", "_"), "pr", str(target_number)),
            worker_id,
        )
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
            rel = repo_relative_path(target_file, wt_path)
            if rel:
                # Check scope.json
                scope_path = artifact_dir / "scope.json"
                scope = load_json(scope_path)
                allowed = scope.get("allowed_paths", [])
                forbidden = scope.get("forbidden_paths", [])

                # Check forbidden
                for fp in forbidden:
                    if rel.startswith(fp) or fp in rel:
                        print(f"⛔ PRFORGE MESH BLOCK: Path is in forbidden scope: {rel}", file=sys.stderr)
                        sys.exit(1)

                # Check path leases (after PLAN / during IMPLEMENT)
                locks_path = artifact_dir / "locks.json"
                locks = load_json(locks_path)
                path_leases = locks.get("leases", {}).get("paths") or []
                if current_phase not in PLAN_PHASES:
                    if not path_leases:
                        print(f"⛔ PRFORGE MESH BLOCK: No coordinator-certified path leases for write: {rel}", file=sys.stderr)
                        print("   Complete PLAN and wait for IMPLEMENT certification before source edits.", file=sys.stderr)
                        sys.exit(1)
                    if not path_is_covered_by_lease(rel, path_leases):
                        print(f"⛔ PRFORGE MESH BLOCK: Path not in leased scope: {rel}", file=sys.stderr)
                        print(f"   Leased paths: {[pl.get('path', '') for pl in path_leases]}", file=sys.stderr)
                        sys.exit(1)

    # For Bash, block dangerous operations outside worktree
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
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
