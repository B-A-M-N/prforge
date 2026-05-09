#!/usr/bin/env bash
# PRForge mesh status — no AI interpretation needed.
set -euo pipefail

python3 << 'PYEOF'
import json, os
from pathlib import Path

mesh_dir = Path.home() / ".prforge-mesh"

coord_pid_f = mesh_dir / "coordinator.pid"
if coord_pid_f.exists():
    pid = int(coord_pid_f.read_text().strip())
    try:
        os.kill(pid, 0)
        print(f"Coordinator: running (PID {pid})")
    except ProcessLookupError:
        print(f"Coordinator: DEAD (stale PID {pid})")
else:
    print("Coordinator: not started")

worker_pids = list(mesh_dir.glob("worker-*.pid"))
if worker_pids:
    print(f"\nWorker daemons ({len(worker_pids)}):")
    for pf in sorted(worker_pids):
        pid = int(pf.read_text().strip())
        try:
            os.kill(pid, 0)
            print(f"  PID {pid}: running  ({pf.name})")
        except ProcessLookupError:
            print(f"  PID {pid}: DEAD     ({pf.name})")
else:
    print("\nNo worker daemons started")

config_path = mesh_dir / "config.json"
if not config_path.exists():
    print("\nNo mesh config found")
    exit(0)

config = json.loads(config_path.read_text())
redis_url = config["mesh"]["redis_url"]
cluster = config["mesh"]["cluster_name"]

try:
    import redis
    r = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
    r.ping()
    print(f"\nRedis: online  {redis_url}")
except Exception as e:
    print(f"\nRedis: OFFLINE ({e})")
    exit(0)

nodes_key = f"Workflow:{cluster}:nodes"
node_ids = r.smembers(nodes_key)
print(f"\nRegistered nodes ({len(node_ids)}):")
for nid in sorted(node_ids):
    n = r.hgetall(f"Workflow:{cluster}:node:{nid}")
    if not n:
        r.srem(nodes_key, nid)
        continue
    ttl = r.ttl(f"Workflow:{cluster}:node:{nid}")
    status = n.get("status", "?")
    role = n.get("roles", "?")
    job = n.get("active_job", "") or "—"
    ghost = "  [GHOST - no TTL]" if ttl == -1 else f"  [TTL {ttl}s]"
    print(f"  {nid:35s}  [{role:20s}]  {status:8s}  {job}{ghost}")

pending = r.xlen(f"Workflow:{cluster}:stream:jobs:pending")
print(f"\nPending jobs: {pending}")
PYEOF
