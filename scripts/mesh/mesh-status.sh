#!/usr/bin/env bash
# PRForge mesh status — no AI interpretation needed.

python3 << 'PYEOF'
import json, os
from pathlib import Path

mesh_dir = Path.home() / ".prforge-mesh"

def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False

coord_pid_f = mesh_dir / "coordinator.pid"
if coord_pid_f.exists():
    pid = int(coord_pid_f.read_text().strip())
    if pid_alive(pid):
        print(f"Coordinator: running (PID {pid})")
    else:
        print(f"Coordinator: DEAD (stale PID {pid})")
        coord_pid_f.unlink(missing_ok=True)
else:
    print("Coordinator: not started")

worker_pids = list(mesh_dir.glob("worker-*.pid"))
live = []
for pf in sorted(worker_pids):
    pid = int(pf.read_text().strip())
    if pid_alive(pid):
        live.append((pid, pf.name))
    else:
        pf.unlink(missing_ok=True)

if live:
    print(f"\nWorker daemons ({len(live)}):")
    for pid, name in live:
        print(f"  PID {pid}: running  ({name})")
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
    ghost = "  [GHOST]" if ttl == -1 else f"  [TTL {ttl}s]"
    print(f"  {nid:35s}  [{role:14s}]  {status:8s}  {job}{ghost}")

pending = r.xlen(f"Workflow:{cluster}:stream:jobs:pending")
print(f"\nPending jobs: {pending}")
PYEOF
