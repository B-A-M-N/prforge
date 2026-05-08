---
name: pr-distributed-local
description: "Configure and control PRForge Local Mesh — vertical scaling on ONE machine."
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# /pr-distributed-local

The arg is one of: `coordinator` | `worker` | `status` | `off`

Find the mesh scripts directory first:

```bash
MESH_SCRIPTS=$(python3 -c "
from pathlib import Path
hits = [p for p in Path.home().rglob('prforge_mesh.py') if '.git' not in str(p)]
print(hits[0].parent if hits else 'NOT_FOUND')
")
echo "Mesh scripts: $MESH_SCRIPTS"
```

If `NOT_FOUND`, stop and tell the user prforge_mesh.py could not be located.

---

## `coordinator`

Run this exact command. Do not paraphrase or split it.

```bash
bash "$MESH_SCRIPTS/start-coordinator.sh"
```

Show the full output to the user. Done.

---

## `worker`

Run this exact command. Do not paraphrase or split it.

```bash
bash "$MESH_SCRIPTS/start-worker.sh"
```

Show the full output to the user. Done.

---

## `status`

```bash
python3 << 'PYEOF'
import json, os
from pathlib import Path

mesh_dir = Path.home() / ".prforge-mesh"

# Daemon status from PID files
coord_pid_f = mesh_dir / "coordinator.pid"
if coord_pid_f.exists():
    pid = int(coord_pid_f.read_text().strip())
    alive = os.kill(pid, 0) is None if True else False
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
            print(f"  PID {pid}: running  ({pf})")
        except ProcessLookupError:
            print(f"  PID {pid}: DEAD     ({pf})")
else:
    print("\nNo worker daemons started")

# Redis mesh state
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
```

---

## `off`

Stop all daemons on this machine.

```bash
MESH_DIR="$HOME/.prforge-mesh"

for pf in "$MESH_DIR"/coordinator.pid "$MESH_DIR"/worker-*.pid; do
  [ -f "$pf" ] || continue
  PID=$(cat "$pf")
  if kill "$PID" 2>/dev/null; then
    echo "Stopped PID $PID ($pf)"
  else
    echo "Already gone: PID $PID ($pf)"
  fi
  rm -f "$pf"
done
echo "Done."
```

---

## Architecture

```
start-coordinator.sh:
  1. Starts Redis (if not running)
  2. Writes ~/.prforge-mesh/config.json
  3. Launches prforge_mesh.py coordinator as background daemon
  4. Flushes stale ghost nodes (TTL=-1) on startup
  Node key TTL is set by heartbeat() — if TTL=-1, it's a ghost from a dead session.

start-worker.sh:
  1. Reads coordinator config for redis_url + cluster_name
  2. Writes ~/.prforge-mesh/worker-template.json (node_id: "auto")
  3. Launches prforge_mesh.py worker as background daemon
  4. Daemon generates its own unique UUID node_id at startup — not from a file,
     not from a shell variable, not from the config. From uuid4() in the process.
  Each worker daemon gets its own PID-named log and pid file.
```
