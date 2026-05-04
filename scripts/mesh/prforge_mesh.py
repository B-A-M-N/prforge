#!/usr/bin/env python3
"""
PRForge Mesh — entry point.

Usage:
  prforge_mesh.py worker
  prforge_mesh.py coordinator
  prforge_mesh.py auditor
  prforge_mesh.py status
  prforge_mesh.py offline
  prforge_mesh.py enqueue --type TYPE --repo ORG/REPO --pr NUMBER --priority P0 --source-url URL
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Scripts live in the same directory; Python path set by service ExecStart
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("prforge.mesh")

CONFIG_PATH = Path.home() / ".prforge-mesh" / "config.json"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        sys.exit(
            f"Config not found: {path}\n"
            "Run /pr-distributed worker (or coordinator/auditor) to initialize."
        )
    with open(path) as f:
        return json.load(f)


def get_cluster(config: dict) -> str:
    return config["mesh"]["cluster_name"]


def get_node_id(config: dict) -> str:
    return config["mesh"]["node_id"]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_worker(config: dict) -> None:
    import redis_backend as rb

    roles = config["mesh"].get("roles", [])
    if "worker" not in roles:
        sys.exit(f"ERROR: This node has roles {roles}. "
                 "Cannot run worker loop without 'worker' role.")
    import worker as w
    r = rb.connect(config["mesh"]["redis_url"])
    w.run(r, get_cluster(config), config)


def cmd_coordinator(config: dict) -> None:
    import redis_backend as rb

    roles = config["mesh"].get("roles", [])
    if "coordinator" not in roles:
        sys.exit(f"ERROR: This node has roles {roles}. "
                 "Cannot run coordinator loop without 'coordinator' role.")
    import coordinator as c
    r = rb.connect(config["mesh"]["redis_url"])
    c.run(r, get_cluster(config), config)


def cmd_auditor(config: dict) -> None:
    import redis_backend as rb

    roles = config["mesh"].get("roles", [])
    if "auditor" not in roles:
        sys.exit(f"ERROR: This node has roles {roles}. "
                 "Cannot run auditor loop without 'auditor' role.")
    import auditor as a
    r = rb.connect(config["mesh"]["redis_url"])
    a.run(r, get_cluster(config), config)


def cmd_status(config: dict) -> None:
    import redis_backend as rb

    r = rb.connect(config["mesh"]["redis_url"])
    cluster = get_cluster(config)
    status = rb.mesh_status(r, cluster)

    print(f"\nPRForge Mesh — cluster: {cluster}")
    print(f"  Pending jobs:       {status['pending_jobs']}")
    print(f"  Active worker jobs: {status['active_worker_jobs']} / 2")
    print(f"\nNodes ({len(status['nodes'])}):")
    for n in status['nodes']:
        roles    = n.get("roles", "?")
        node_id  = n.get("node_id", "?")
        stat     = n.get("status", "?")
        cap      = n.get("capacity", "0")
        active   = n.get("active_job", "") or "(none)"
        seen     = n.get("last_seen", "?")
        print(f"  {node_id:20s}  roles={roles:25s} status={stat:8s} "
              f"capacity={cap}  job={active:40s}  last_seen={seen}")

    print()


def cmd_offline(config: dict) -> None:
    import redis_backend as rb

    r = rb.connect(config["mesh"]["redis_url"])
    cluster = get_cluster(config)
    node_id = get_node_id(config)
    rb.mark_offline(r, cluster, node_id)
    print(f"Node {node_id} marked offline.")


def cmd_enqueue(config: dict, args: argparse.Namespace) -> None:
    from datetime import datetime, timezone
    import redis_backend as rb
    from notifications import notify

    r = rb.connect(config["mesh"]["redis_url"])
    cluster = get_cluster(config)

    repo     = args.repo
    pr_num   = str(args.pr)
    job_type = args.type
    priority = args.priority
    url      = args.source_url

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    slug = repo.replace("/", "_")
    job_id = f"job_{slug}_{pr_num}_{job_type}_{ts}"

    # We need head/base branch — fetch from gh if possible
    base_branch = "main"
    head_branch = ""
    try:
        import subprocess
        result = subprocess.run(
            ["gh", "pr", "view", pr_num, "--repo", repo,
             "--json", "headRefName,baseRefName"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            head_branch = data.get("headRefName", "")
            base_branch = data.get("baseRefName", "main")
    except Exception:
        pass

    job = {
        "job_id":      job_id,
        "type":        job_type,
        "priority":    priority,
        "repo":        repo,
        "pr_number":   int(pr_num),
        "base_branch": base_branch,
        "head_branch": head_branch,
        "source_url":  url,
        "created_by":  "manual",
        "status":      "queued",
        "created_at":  datetime.now(timezone.utc).isoformat(),
    }

    rb.enqueue_job(r, cluster, job)
    notify(r, cluster, "JobQueued",
           f"Manual job {job_id} queued for {repo}#{pr_num}")
    print(f"Queued: {job_id}")
    print(f"  type={job_type} priority={priority} repo={repo} pr={pr_num}")


def cmd_manager_mode(config: dict, args: argparse.Namespace) -> None:
    """Handle manager-mode subcommand: off | certify-only | internal-actions | low-risk-public | status."""
    from pathlib import Path

    sub = args.subcommand
    cfg_path = Path(CONFIG_PATH)

    if sub == "status":
        mgr = config.get("manager_mode", {})
        enabled = mgr.get("enabled", False)
        authority = mgr.get("authority", "off")
        print(f"\nManager Mode: {'enabled' if enabled else 'disabled'}")
        print(f"  authority: {authority}")
        if enabled:
            print(f"  require_coordinator_pass: {mgr.get('require_coordinator_pass', True)}")
            print(f"  require_auditor_pass: {mgr.get('require_auditor_pass', True)}")
            print(f"  max_risk: {mgr.get('max_risk', 'medium')}")
            print(f"  allowed_public_actions: {mgr.get('allowed_public_actions', [])}")
            print(f"  forbidden_public_actions: {mgr.get('forbidden_public_actions', [])}")
        return

    # Map subcommand to authority
    authority_map = {
        "off": "off",
        "certify-only": "certify_only",
        "internal-actions": "internal_actions",
        "low-risk-public": "low_risk_public",
    }
    authority = authority_map.get(sub, "off")

    # Default manager_mode configs per authority
    defaults = {
        "enabled": sub != "off",
        "authority": authority,
        "require_coordinator_pass": True,
        "require_auditor_pass": True,
        "require_clean_validation": True,
        "require_review_freshness": True,
        "require_ci_relatedness_clean": True,
        "require_no_unknown_ci_for_auto_ship": True,
        "require_no_scope_delta": True,
        "require_dod_evidence": True,
        "require_artifact_exclusion": True,
        "max_risk": "medium",
        "auto_requeue_on_fail": True,
        "auto_certify_on_pass": True,
        "auto_public_actions": False,
        "allowed_public_actions": ["push", "comment"] if sub == "low-risk-public" else [],
        "forbidden_public_actions": ["force_push", "merge", "delete_branch"],
    }

    config.setdefault("intel", {
        "enabled": True,
        "global_index_path": str(Path.home() / ".prforge-intel"),
        "local_cache_enabled": True,
        "embedding_provider": "fastembed",
        "reranker_provider": "fastembed",
        "high_risk_threshold": 0.80,
        "medium_risk_threshold": 0.60,
        "fail_safe_mode": "deterministic_only",
    })

    config["manager_mode"] = defaults
    cfg_path.write_text(json.dumps(config, indent=2))
    print(f"Manager Mode set to: {sub} (authority={authority})")
    print(f"Config updated: {cfg_path}")

    import os
    if not os.environ.get("PRFORGE_MESH_SIGNING_KEY"):
        print("\n⚠️  WARNING: PRFORGE_MESH_SIGNING_KEY is not set.")
        print("   Manager Mode verdicts cannot be signed without it.")
        print("   Export it: export PRFORGE_MESH_SIGNING_KEY=<your-secret>")


def cmd_policy_check(args: argparse.Namespace) -> None:
    import policy_engine

    decision = policy_engine.check_policy(
        event=args.event,
        phase=args.phase,
        run_dir=Path(args.run_dir),
        repo=Path(args.repo) if args.repo else None,
    )
    if args.write:
        policy_engine.write_decision_artifacts(Path(args.run_dir), decision)
    print(json.dumps(decision, indent=2))


def cmd_intel_preflight(args: argparse.Namespace) -> None:
    import intel_engine

    result = intel_engine.preflight(
        embedding_model=args.embedding_model,
        reranker_model=args.reranker_model,
        home=Path(args.intel_home).expanduser() if args.intel_home else None,
    )
    print(json.dumps(result, indent=2))
    if args.require_ready and not result.get("ready"):
        sys.exit(1)


def cmd_intel_index(args: argparse.Namespace) -> None:
    import intel_engine

    result = intel_engine.index_run(
        run_dir=Path(args.run_dir),
        embedding_model=args.embedding_model or None,
        home=Path(args.intel_home).expanduser() if args.intel_home else None,
    )
    print(json.dumps(result, indent=2))


def cmd_intel_query(args: argparse.Namespace) -> None:
    import intel_engine

    result = intel_engine.query_run(
        run_dir=Path(args.run_dir),
        query=args.query,
        top_k=args.top_k,
        recall_k=args.recall_k,
        home=Path(args.intel_home).expanduser() if args.intel_home else None,
    )
    print(json.dumps(result, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="PRForge Mesh daemon")
    parser.add_argument("--config", help="Path to config.json")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("worker")
    sub.add_parser("coordinator")
    sub.add_parser("auditor")
    sub.add_parser("status")
    sub.add_parser("offline")

    enq = sub.add_parser("enqueue")
    enq.add_argument("--type",       required=True,
                     choices=["review_response", "pr_polish",
                              "ci_fix_related_to_branch", "audit_only"])
    enq.add_argument("--repo",       required=True)
    enq.add_argument("--pr",         required=True, type=int)
    enq.add_argument("--priority",   required=True,
                     choices=["P0", "P1", "P2", "P3", "P4"])
    enq.add_argument("--source-url", required=True)

    mgr = sub.add_parser("manager-mode")
    mgr.add_argument("subcommand", choices=[
        "off", "certify-only", "internal-actions", "low-risk-public", "status",
    ])

    policy = sub.add_parser("policy-check")
    policy.add_argument("--event", required=True,
                        choices=["phase_exit", "public_action", "push",
                                 "post_comment", "create_pr"])
    policy.add_argument("--phase", required=True)
    policy.add_argument("--run-dir", required=True)
    policy.add_argument("--repo", default="")
    policy.add_argument("--write", action="store_true")

    intel_pf = sub.add_parser("intel-preflight")
    intel_pf.add_argument("--embedding-model", default="BAAI/bge-small-en-v1.5")
    intel_pf.add_argument("--reranker-model", default="Xenova/ms-marco-MiniLM-L-6-v2")
    intel_pf.add_argument("--intel-home", default="")
    intel_pf.add_argument("--require-ready", action="store_true")

    intel_ix = sub.add_parser("intel-index")
    intel_ix.add_argument("--run-dir", required=True)
    intel_ix.add_argument("--embedding-model", default="")
    intel_ix.add_argument("--intel-home", default="")

    intel_q = sub.add_parser("intel-query")
    intel_q.add_argument("--run-dir", required=True)
    intel_q.add_argument("--query", required=True)
    intel_q.add_argument("--top-k", type=int, default=5)
    intel_q.add_argument("--recall-k", type=int, default=50)
    intel_q.add_argument("--intel-home", default="")

    args = parser.parse_args()
    if args.command == "policy-check":
        cmd_policy_check(args)
        return
    if args.command == "intel-preflight":
        cmd_intel_preflight(args)
        return
    if args.command == "intel-index":
        cmd_intel_index(args)
        return
    if args.command == "intel-query":
        cmd_intel_query(args)
        return

    config_path = Path(args.config) if args.config else Path(os.environ.get("PRFORGE_MESH_CONFIG", CONFIG_PATH))
    config = load_config(config_path)

    dispatch = {
        "worker":      cmd_worker,
        "coordinator": cmd_coordinator,
        "auditor":     cmd_auditor,
        "status":      cmd_status,
        "offline":     cmd_offline,
    }

    if args.command == "enqueue":
        cmd_enqueue(config, args)
    elif args.command == "manager-mode":
        cmd_manager_mode(config, args)
    else:
        fn = dispatch.get(args.command)
        if fn:
            fn(config)
        else:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
