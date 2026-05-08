---
name: pr-distributed-local
description: "Configure and control PRForge Local Mesh — vertical scaling on ONE machine (coordinator + workers on same box)."
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# /pr-distributed-local — PRForge Vertical Mesh (Single Machine)

You are executing a PRForge Vertical Mesh command.
This runs actual daemon processes on the SAME machine:
- One coordinator daemon (dispatches jobs to workers)
- One or more worker daemons (pick up and run PRForge jobs)

**Vertical scaling** = more worker daemons on one box.
**Horizontal scaling** = more machines on LAN → use `/pr-distributed`.

Each worker daemon gets a unique node ID. Never share config files between workers.

Follow these instructions exactly. Use your Bash tool to create real files and start real processes.

## Parse the argument

The user typed `/pr-distributed-local <arg>`. The arg is one of:
`coordinator` | `worker` | `status` | `off`

---

## ACTION: `coordinator`

Sets up THIS machine as the local mesh coordinator. Writes config, starts the coordinator daemon.

### Step 1: Check prerequisites

```bash
python3 --version
python3 -c "import redis; print(redis.__version__)" 2>/dev/null || echo "REDIS_MISSING"
gh auth status 2>&1 | head -3
MESH_SCRIPTS=$(python3 -c "from pathlib import Path; p = list(Path.home().rglob('prforge_mesh.py')); print(p[0].parent if p else 'NOT_FOUND')")
echo "Mesh scripts: $MESH_SCRIPTS"
```

If redis-py is missing: `pip install redis>=4.6.0`
If `gh` auth fails: warn "gh auth login required for auditing"
If `MESH_SCRIPTS` is `NOT_FOUND`: error — cannot start daemons without the scripts.

### Step 2: Create mesh directories

```bash
mkdir -p ~/.prforge-mesh/checkouts ~/.prforge-mesh/logs
mkdir -p ~/.prforge/repos ~/.prforge/worktrees ~/.prforge/quarantine
```

### Step 3: Start or validate local Redis

```bash
REDIS_PORT=6380
if redis-cli -p $REDIS_PORT ping 2>/dev/null | grep -q PONG; then
  echo "✓ Redis already running on port $REDIS_PORT"
else
  if command -v redis-server >/dev/null 2>&1; then
    for port in 6380 6381 6382 6383 6384 6385 6386 6387 6388 6389; do
      if ! redis-cli -p $port ping 2>/dev/null | grep -q PONG; then
        REDIS_PORT=$port
        break
      fi
    done
    REDIS_DIR="$HOME/.prforge-mesh/redis"
    mkdir -p "$REDIS_DIR"
    cat > "$REDIS_DIR/redis-local.conf" <<REDIS_CONF
port $REDIS_PORT
daemonize yes
dir $REDIS_DIR
loglevel notice
save ""
appendonly no
REDIS_CONF
    redis-server "$REDIS_DIR/redis-local.conf"
    sleep 1
    if redis-cli -p $REDIS_PORT ping 2>/dev/null | grep -q PONG; then
      echo "✓ Redis started on port $REDIS_PORT"
    else
      echo "ERROR: Failed to start Redis"
      exit 1
    fi
  else
    echo "ERROR: redis-server not found. Install Redis first."
    exit 1
  fi
fi
echo "REDIS_PORT=$REDIS_PORT"
```

### Step 4: Write coordinator config

Write the config in the schema that `prforge_mesh.py` expects (`mesh.cluster_name`, `mesh.node_id`, `mesh.redis_url`):

```bash
REDIS_PORT=$(redis-cli -p 6380 ping 2>/dev/null | grep -q PONG && echo 6380 || redis-cli -p 6381 ping 2>/dev/null | grep -q PONG && echo 6381 || echo 6380)

cat > ~/.prforge-mesh/config.json <<EOF
{
  "mesh": {
    "cluster_name": "local",
    "node_id": "coordinator-local",
    "roles": ["coordinator", "auditor"],
    "redis_url": "redis://127.0.0.1:$REDIS_PORT/0"
  },
  "limits": {
    "lease_ttl_seconds": 1800,
    "heartbeat_interval_seconds": 15
  },
  "notifications": {
    "desktop": false,
    "pubsub": true
  },
  "paths": {
    "repo_cache_root": "$HOME/.prforge/repos",
    "worktree_root": "$HOME/.prforge/worktrees",
    "quarantine_root": "$HOME/.prforge/quarantine",
    "checkout_meta_root": "$HOME/.prforge-mesh/checkouts"
  },
  "max_workers": 3
}
EOF
echo "✓ Coordinator config written"
cat ~/.prforge-mesh/config.json
```

### Step 5: Start coordinator daemon

```bash
MESH_SCRIPTS=$(python3 -c "from pathlib import Path; p = list(Path.home().rglob('prforge_mesh.py')); print(p[0].parent if p else '')")
LOG="$HOME/.prforge-mesh/logs/coordinator.log"
PID_FILE="$HOME/.prforge-mesh/coordinator.pid"

# Kill any existing coordinator daemon
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  kill "$OLD_PID" 2>/dev/null && echo "Stopped previous coordinator (PID $OLD_PID)"
  rm -f "$PID_FILE"
fi

cd "$MESH_SCRIPTS"
nohup python3 prforge_mesh.py coordinator > "$LOG" 2>&1 &
DAEMON_PID=$!
echo $DAEMON_PID > "$PID_FILE"
sleep 2

# Verify it's running
if kill -0 "$DAEMON_PID" 2>/dev/null; then
  echo "✓ Coordinator daemon started (PID $DAEMON_PID)"
  echo "  Log: $LOG"
  tail -5 "$LOG"
else
  echo "ERROR: Coordinator daemon failed to start"
  cat "$LOG"
  exit 1
fi
```

Print: "✓ coordinator online — dispatching jobs"
Print: "Redis: 127.0.0.1:<port>"
Print: "Log: ~/.prforge-mesh/logs/coordinator.log"

---

## ACTION: `worker`

Starts a worker daemon. The daemon generates its own unique node ID at startup —
do NOT generate an ID in bash and try to pass it in. That approach is fragile and
has caused duplicate IDs. The config just needs `roles: ["worker"]`; the daemon
handles everything else.

### Step 1: Write shared worker config (once — all workers reuse this file)

```bash
python3 << 'PYEOF'
import json, sys
from pathlib import Path

coord_config_path = Path.home() / ".prforge-mesh" / "config.json"
if not coord_config_path.exists():
    print("ERROR: Coordinator config not found. Run /pr-distributed-local coordinator first.")
    sys.exit(1)

coord = json.loads(coord_config_path.read_text())
redis_url = coord["mesh"]["redis_url"]
cluster   = coord["mesh"]["cluster_name"]

worker_config = {
    "mesh": {
        "cluster_name": cluster,
        "node_id":      "auto",   # daemon generates a real UUID at startup
        "roles":        ["worker"],
        "redis_url":    redis_url,
    },
    "worker": {
        "repo_roots": [str(Path.home())],
        "capacity":   1,
    },
    "limits": {
        "lease_ttl_seconds":        1800,
        "heartbeat_interval_seconds": 15,
    },
    "notifications": {"desktop": False, "pubsub": True},
}

out = Path.home() / ".prforge-mesh" / "worker-template.json"
out.write_text(json.dumps(worker_config, indent=2))
print(f"✓ Worker template written: {out}")
print(f"  cluster: {cluster}  redis: {redis_url}")
PYEOF
```

### Step 2: Start worker daemon

The daemon reads the template, then immediately replaces `node_id: auto` with a
fresh `uuid4()` in memory. Each `nohup python3 prforge_mesh.py worker` invocation
gets a different PID and a different UUID — no bash variable passing needed.

```bash
MESH_SCRIPTS=$(python3 -c "from pathlib import Path; p = list(Path.home().rglob('prforge_mesh.py')); print(p[0].parent if p else 'NOT_FOUND')")
if [ "$MESH_SCRIPTS" = "NOT_FOUND" ]; then
  echo "ERROR: prforge_mesh.py not found"
  exit 1
fi

mkdir -p "$HOME/.prforge-mesh/logs"
cd "$MESH_SCRIPTS"

# Start daemon — use its own PID for log/pid file naming (unique per process)
nohup python3 prforge_mesh.py --config "$HOME/.prforge-mesh/worker-template.json" worker \
  > "$HOME/.prforge-mesh/logs/worker-startup-$$.log" 2>&1 &
DAEMON_PID=$!

# Rename log to use daemon PID now that we have it
mv "$HOME/.prforge-mesh/logs/worker-startup-$$.log" \
   "$HOME/.prforge-mesh/logs/worker-$DAEMON_PID.log" 2>/dev/null || true
echo $DAEMON_PID > "$HOME/.prforge-mesh/worker-$DAEMON_PID.pid"

sleep 2

if kill -0 "$DAEMON_PID" 2>/dev/null; then
  WORKER_ID=$(grep "worker node_id:" "$HOME/.prforge-mesh/logs/worker-$DAEMON_PID.log" 2>/dev/null | awk '{print $NF}')
  echo "✓ Worker daemon started (PID $DAEMON_PID)"
  echo "  node_id: ${WORKER_ID:-<see log>}"
  echo "  Log: $HOME/.prforge-mesh/logs/worker-$DAEMON_PID.log"
  tail -5 "$HOME/.prforge-mesh/logs/worker-$DAEMON_PID.log"
else
  echo "ERROR: Worker daemon failed to start"
  cat "$HOME/.prforge-mesh/logs/worker-$DAEMON_PID.log" 2>/dev/null
  exit 1
fi
```

Print: "✓ worker online — polling for jobs every 15s"
Print the node_id and log path from the output above.

---

## ACTION: `status`

Show running daemons and Redis mesh state.

```bash
python3 << 'PYEOF'
import json, subprocess, os
from pathlib import Path

mesh_dir = Path.home() / ".prforge-mesh"

# Coordinator daemon status
coord_pid_file = mesh_dir / "coordinator.pid"
coord_running = False
if coord_pid_file.exists():
    pid = int(coord_pid_file.read_text().strip())
    try:
        os.kill(pid, 0)
        coord_running = True
        print(f"Coordinator: running (PID {pid})")
    except ProcessLookupError:
        print(f"Coordinator: DEAD (stale PID {pid})")
else:
    print("Coordinator: not started")

# Worker daemon status
worker_pids = list(mesh_dir.glob("worker-*.pid"))
if worker_pids:
    print(f"\nWorkers ({len(worker_pids)}):")
    for pf in worker_pids:
        wid = pf.stem.replace("worker-", "", 1)
        pid = int(pf.read_text().strip())
        try:
            os.kill(pid, 0)
            print(f"  {wid}: running (PID {pid})")
        except ProcessLookupError:
            print(f"  {wid}: DEAD (stale PID {pid})")
else:
    print("\nWorkers: none started")

# Redis mesh state
config_path = mesh_dir / "config.json"
if not config_path.exists():
    print("\nNo mesh config — coordinator not initialized")
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

print()
nodes_key = f"Workflow:{cluster}:nodes"
node_ids = r.smembers(nodes_key)
for nid in sorted(node_ids):
    n = r.hgetall(f"Workflow:{cluster}:node:{nid}")
    if not n:
        r.srem(nodes_key, nid)
        continue
    role = n.get("roles", "?")
    status = n.get("status", "?")
    job = n.get("active_job", "") or "—"
    print(f"  {nid:30s}  [{role:20s}]  {status:8s}  {job}")

pending = r.xlen(f"Workflow:{cluster}:stream:jobs:pending")
print(f"\nPending jobs: {pending}")
PYEOF
```

---

## ACTION: `off`

Stop daemon(s) on this machine. If PRFORGE_WORKER_ID is set in env, stops just that worker.
Otherwise stops all workers and the coordinator.

```bash
MESH_DIR="$HOME/.prforge-mesh"

stop_pid_file() {
  local pf="$1"
  local label="$2"
  if [ -f "$pf" ]; then
    PID=$(cat "$pf")
    if kill "$PID" 2>/dev/null; then
      echo "✓ Stopped $label (PID $PID)"
    else
      echo "  $label already stopped (stale PID $PID)"
    fi
    rm -f "$pf"
  fi
}

if [ -n "$PRFORGE_WORKER_ID" ]; then
  # Stop just this worker
  stop_pid_file "$MESH_DIR/worker-$PRFORGE_WORKER_ID.pid" "$PRFORGE_WORKER_ID"
  rm -f "$MESH_DIR/worker-config-$PRFORGE_WORKER_ID.json"
else
  # Stop everything
  for pf in "$MESH_DIR"/worker-*.pid; do
    [ -f "$pf" ] && stop_pid_file "$pf" "$(basename $pf .pid)"
  done
  stop_pid_file "$MESH_DIR/coordinator.pid" "coordinator"
fi
```

---

## Guards

- Never share config.json between workers — each worker gets its own file named worker-config-<id>.json
- Never reuse node IDs across workers — the UUID in node_id must be unique per daemon
- `off` stops ONLY the current node (if PRFORGE_WORKER_ID is set), otherwise all nodes
- Do not touch system Redis on port 6379
- Always verify daemon is actually running after `nohup ... &` by checking `kill -0 $PID`
- If the daemon crashes at startup, show the log and stop — do not claim success

---

## Architecture

```
Original repo (read-only reference)
  ↓ git worktree add
Worker A worktree → branch prforge/3819-fix-abc123
Worker B worktree → branch prforge/3848-fix-def456

Coordinator daemon (prforge_mesh.py coordinator):
  - Polls Redis every 5s
  - Finds idle workers, acquires leases, assigns jobs
  - Writes status:assigned to job hash + updates worker.active_job

Worker daemon (prforge_mesh.py --config worker-config-<id>.json worker):
  - Heartbeats every 15s (node key expires in 45s without heartbeat)
  - Polls own node hash for active_job field
  - On job assigned: writes .prforge/inbox/job.json in repo
  - /pr-continue picks up the inbox and runs the workflow

Redis (coordination plane):
  Workflow:local:nodes                   → set of node IDs
  Workflow:local:node:<id>               → hash: status, active_job, roles
  Workflow:local:stream:jobs:pending     → stream of pending jobs
  Workflow:local:lease:job:<id>          → job ownership lease (TTL 1800s)
  Workflow:local:lease:target:<repo>:pr:<N>  → PR uniqueness lease
  Workflow:local:lease:branch:<repo>:<branch> → branch uniqueness lease
  Workflow:local:lease:worker:<node_id>  → worker busy lease
```

---

## Verification

```
Terminal 1: /pr-distributed-local coordinator
  → "✓ Coordinator daemon started (PID 12345)"

Terminal 2: /pr-distributed-local worker
  → "✓ Worker daemon started: worker-hostname-a1b2c3d4 (PID 12346)"

Terminal 3: /pr-distributed-local worker
  → "✓ Worker daemon started: worker-hostname-e5f6g7h8 (PID 12347)"

Any terminal: /pr-distributed-local status
  → coordinator-local         [coordinator,auditor]  online   —
  → worker-hostname-a1b2c3d4  [worker              ]  idle     —
  → worker-hostname-e5f6g7h8  [worker              ]  idle     —
  → Pending jobs: 0
```
