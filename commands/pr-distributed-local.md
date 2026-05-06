---
name: pr-distributed-local
description: "Configure and control PRForge Local Mesh — vertical scaling on ONE machine (watchtower + workers on same box)."
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# /pr-distributed-local — PRForge Vertical Mesh (Single Machine)

You are executing a PRForge Vertical Mesh command.
This runs multiple Claude Code instances on the SAME machine:
- One coordinator (managing + auditing)
- One or more workers (editing agents)

**Vertical scaling** = more workers on one box.
**Horizontal scaling** = more machines on LAN → use `/pr-distributed`.

Each worker gets an **isolated worktree** — never the original repo checkout.

Follow these instructions exactly. Use your Write and Bash tools to create real files on disk.

## Parse the argument

The user typed `/pr-distributed-local <arg>`. The arg is one of:
`coordinator` | `worker` | `status` | `off`

---

## ACTION: `coordinator`

Sets up THIS machine as the local mesh boss. Creates Redis, worktree roots, and config.

### Step 1: Check prerequisites

```bash
python3 --version
python3 -c "import redis; print(redis.__version__)" 2>/dev/null || echo "REDIS_MISSING"
gh auth status 2>&1 | head -3
```

If redis-py is missing: `pip install redis>=4.6.0`
If `gh` auth fails: warn "gh auth login required for auditing"

### Step 2: Create mesh directories

```bash
mkdir -p ~/.prforge-mesh/checkouts
mkdir -p ~/.prforge/repos
mkdir -p ~/.prforge/worktrees
mkdir -p ~/.prforge/quarantine
```

### Step 3: Start or validate local Redis

```bash
# Check if Redis is already running on our port
REDIS_PORT=6380
if redis-cli -p $REDIS_PORT ping 2>/dev/null | grep -q PONG; then
  echo "✓ Redis already running on port $REDIS_PORT"
else
  # Try to start local Redis
  if command -v redis-server >/dev/null 2>&1; then
    # Find available port
    for port in 6380 6381 6382 6383 6384 6385 6386 6387 6388 6389; do
      if ! redis-cli -p $port ping 2>/dev/null | grep -q PONG; then
        REDIS_PORT=$port
        break
      fi
    done

    # Generate Redis config
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
```

### Step 4: Write mesh config

```bash
cat > ~/.prforge-mesh/config.json <<EOF
{
  "mode": "local",
  "cluster": "local",
  "roles": ["coordinator", "auditor"],
  "redis": {
    "host": "127.0.0.1",
    "port": $REDIS_PORT,
    "url": "redis://127.0.0.1:$REDIS_PORT/0"
  },
  "paths": {
    "repo_cache_root": "$HOME/.prforge/repos",
    "worktree_root": "$HOME/.prforge/worktrees",
    "quarantine_root": "$HOME/.prforge/quarantine",
    "checkout_meta_root": "$HOME/.prforge-mesh/checkouts"
  },
  "max_workers": 3,
  "lock_ttl_seconds": 600
}
EOF
echo "✓ Mesh config written"
```

### Step 5: Register coordinator in Redis

```bash
python3 << 'PYEOF'
import json, redis, time
from pathlib import Path

config = json.loads(Path.home().joinpath(".prforge-mesh/config.json").read_text())
r = redis.Redis.from_url(config["redis"]["url"], decode_responses=True)

node_id = "coordinator-local"
r.hset(f"Workflow:{config['cluster']}:node:{node_id}", mapping={
    "node_id": node_id,
    "roles": "coordinator,auditor",
    "status": "online",
    "capacity": str(config["max_workers"]),
    "active_job": "",
    "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
})
r.sadd(f"Workflow:{config['cluster']}:nodes", node_id)
print(f"✓ Coordinator registered: {node_id}")
PYEOF
```

Print: "✓ coordinator online — managing + auditing"
Print: "Max workers: 3"
Print: "Redis: 127.0.0.1:$REDIS_PORT"

---

## ACTION: `worker`

Registers this Claude instance as a worker. It will:
1. Register in Redis
2. Wait for job assignment
3. Create isolated worktree for each job
4. Run PRForge workflow inside worktree

### Step 1: Read mesh config

```bash
CONFIG=$(cat ~/.prforge-mesh/config.json)
REDIS_URL=$(echo "$CONFIG" | python3 -c "import json,sys; print(json.load(sys.stdin)['redis']['url'])")
CLUSTER=$(echo "$CONFIG" | python3 -c "import json,sys; print(json.load(sys.stdin).get('cluster','local'))")
```

### Step 2: Generate worker ID and register

```bash
WORKER_ID="worker-$(hostname)-$$"
# Or use a stable ID if available
if [ -f ~/.prforge-mesh/worker-id ]; then
  WORKER_ID=$(cat ~/.prforge-mesh/worker-id)
else
  echo "$WORKER_ID" > ~/.prforge-mesh/worker-id
fi

python3 << PYEOF
import json, redis, time
from pathlib import Path

config = json.loads(Path.home().joinpath(".prforge-mesh/config.json").read_text())
r = redis.Redis.from_url(config["redis"]["url"], decode_responses=True)

node_id = "$WORKER_ID"
r.hset(f"Workflow:{config['cluster']}:node:{node_id}", mapping={
    "node_id": node_id,
    "roles": "worker",
    "status": "idle",
    "capacity": "1",
    "active_job": "",
    "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "repo_roots": str(Path.home()),
})
r.sadd(f"Workflow:{config['cluster']}:nodes", node_id)
print(f"✓ Worker registered: {node_id}")
PYEOF
```

### Step 3: Write worker config for this session

The worker needs to know its own ID for lock checks:

```bash
# Add worker_id to the mesh config for this session
python3 << 'PYEOF'
import json
from pathlib import Path

config_path = Path.home() / ".prforge-mesh" / "config.json"
config = json.loads(config_path.read_text())
# Worker ID is written to a separate file for hooks to read
worker_id_path = Path.home() / ".prforge-mesh" / "worker-id"
if worker_id_path.exists():
    config["worker_id"] = worker_id_path.read_text().strip()
    config_path.write_text(json.dumps(config, indent=2))
    print(f"✓ Worker ID set: {config['worker_id']}")
PYEOF
```

Print: "✓ worker online — waiting for jobs"
Print: "Worker ID: $WORKER_ID"

**After this, the worker enters the job loop.** Instruct the worker to:
1. Poll Redis for assigned jobs
2. For each job: acquire target lock → create worktree → cd into worktree → run PRForge pipeline
5. After PLAN: acquire path leases
6. Before push/PR: acquire public lease, require /pr-approve

---

## ACTION: `status`

Show the local mesh status.

```bash
python3 << 'PYEOF'
import json, redis
from pathlib import Path

config = json.loads(Path.home().joinpath(".prforge-mesh/config.json").read_text())
r = redis.Redis.from_url(config["redis"]["url"], decode_responses=True)
cluster = config["cluster"]

print("PRForge Vertical Mesh (Local)")
print()

# Redis status
try:
    r.ping()
    print(f"Redis:  online  {config['redis']['host']}:{config['redis']['port']}")
except:
    print(f"Redis:  OFFLINE")
print()

# Nodes
nodes_key = f"Workflow:{cluster}:nodes"
node_ids = r.smembers(nodes_key)
workers = []
coordinator = None
for nid in node_ids:
    n = r.hgetall(f"Workflow:{cluster}:node:{nid}")
    if not n:
        r.srem(nodes_key, nid)
        continue
    roles = n.get("roles", "")
    if "coordinator" in roles:
        coordinator = n
    if "worker" in roles:
        workers.append(n)

if coordinator:
    print(f"Coordinator: {coordinator['status']:10s}  {coordinator['node_id']}")
else:
    print("Coordinator: OFFLINE")

print()
if workers:
    print("Workers:")
    for w in workers:
        job = w.get("active_job", "") or "idle"
        print(f"  {w['node_id']:20s}  {w['status']:10s}  {job}")
else:
    print("Workers: none registered")

print()

# Checkouts
checkouts_dir = Path.home() / ".prforge-mesh" / "checkouts"
if checkouts_dir.exists():
    active = [f for f in checkouts_dir.glob("*.json") if json.loads(f.read_text()).get("state") == "active"]
    if active:
        print("Checkouts:")
        for f in active:
            meta = json.loads(f.read_text())
            print(f"  {meta['job_id']:20s}  {meta['worker_id']:12s}  {meta['branch']}")
            print(f"    {meta['worktree']}")

print()

# Leases
from redis_backend import list_all_leases
leases = list_all_leases(r, cluster)
if leases:
    print(f"Active leases: {len(leases)}")
    for lk in leases[:20]:
        print(f"  {lk.get('worker_id', '?'):12s}  {lk.get('job_id', '?'):12s}  {lk.get('key', '?')}")
PYEOF
```

---

## ACTION: `off`

Stop ONLY this machine's node.

```bash
python3 << 'PYEOF'
import json, redis
from pathlib import Path

config_path = Path.home() / ".prforge-mesh" / "config.json"
if not config_path.exists():
    exit(0)

config = json.loads(config_path.read_text())
worker_id_path = Path.home() / ".prforge-mesh" / "worker-id"

worker_id = ""
if worker_id_path.exists():
    worker_id = worker_id_path.read_text().strip()

r = redis.Redis.from_url(config["redis"]["url"], decode_responses=True)
cluster = config["cluster"]

# Mark offline
node_key = f"Workflow:{cluster}:node:{worker_id}"
r.hset(node_key, "status", "offline")

# Release all leases for this worker
from redis_backend import list_all_leases
leases = list_all_leases(r, cluster)
for lk in leases:
    if lk.get("worker_id") == worker_id:
        r.delete(lk["key"])
        print(f"  Released: {lk['key']}")

# Remove from nodes set
r.srem(f"Workflow:{cluster}:nodes", worker_id)

print(f"✓ {worker_id} stopped")
PYEOF
```

---

## Guards

- Never suggest running `systemctl` commands manually — meshctl owns that
- Never expose Redis URLs, ports, or config paths to the user
- Workers wait for coordinator gracefully — no hard-fail
- `off` stops ONLY the current node, never the whole mesh
- Do not touch system Redis on port 6379
- Each worker MUST work in its assigned worktree, never the original repo

---

## Architecture

```
Original repo (read-only reference)
  ↓ git worktree add
Worker A worktree → branch prforge/3819-fix-abc123
Worker B worktree → branch prforge/3848-fix-def456
Worker C worktree → branch prforge/3856-fix-ghi789

Redis (coordination plane):
  lease:job:<job_id>          → worker owns job
  lease:target:<repo>/pr/<N>  → one worker per PR
  lease:branch:<repo>:<branch> → one worker per branch
  lease:path:<repo>:<file>     → one worker per file (after PLAN)
  lease:public:<repo>:<branch> → serialize push/PR actions
```

---

## Verification

```bash
# Terminal 1:
/pr-distributed-local coordinator
# → "✓ coordinator online — managing + auditing"

# Terminal 2:
/pr-distributed-local worker
# → "✓ worker online — waiting for jobs"

# Terminal 3:
/pr-distributed-local worker
# → "✓ worker online — waiting for jobs"

/pr-distributed-local status
# → coordinator  online    coordinator-local
# → worker-a   idle        worker-hostname-12345
# → worker-b   idle        worker-hostname-67890
```
