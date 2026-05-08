#!/usr/bin/env bash
# PRForge mesh coordinator startup — runs everything, no AI interpretation needed.
# Usage: bash start-coordinator.sh
set -euo pipefail

MESH_DIR="$HOME/.prforge-mesh"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$MESH_DIR/logs/coordinator.log"
PID_FILE="$MESH_DIR/coordinator.pid"

# ── prereqs ──────────────────────────────────────────────────────────────────
python3 -c "import redis" 2>/dev/null || { echo "ERROR: redis-py missing. Run: pip install redis>=4.6.0"; exit 1; }

# ── directories ──────────────────────────────────────────────────────────────
mkdir -p "$MESH_DIR/logs" "$MESH_DIR/checkouts" "$MESH_DIR/redis" \
         "$HOME/.prforge/repos" "$HOME/.prforge/worktrees" "$HOME/.prforge/quarantine"

# ── redis ────────────────────────────────────────────────────────────────────
REDIS_PORT=6380
for port in 6380 6381 6382 6383 6384 6385 6386 6387 6388 6389; do
  if redis-cli -p "$port" ping 2>/dev/null | grep -q PONG; then
    REDIS_PORT=$port
    echo "Redis already running on port $REDIS_PORT"
    break
  fi
  if [ "$port" = "6380" ] && ! redis-cli -p 6380 ping 2>/dev/null | grep -q PONG; then
    REDIS_PORT=6380
    cat > "$MESH_DIR/redis/redis-local.conf" <<REDIS_CONF
port $REDIS_PORT
daemonize yes
dir $MESH_DIR/redis
loglevel notice
save ""
appendonly no
REDIS_CONF
    redis-server "$MESH_DIR/redis/redis-local.conf"
    sleep 1
    if redis-cli -p "$REDIS_PORT" ping 2>/dev/null | grep -q PONG; then
      echo "Redis started on port $REDIS_PORT"
      break
    else
      echo "ERROR: Failed to start Redis on port $REDIS_PORT"
      exit 1
    fi
  fi
done

# ── config ───────────────────────────────────────────────────────────────────
cat > "$MESH_DIR/config.json" <<EOF
{
  "mesh": {
    "cluster_name": "local",
    "node_id": "coordinator-local",
    "roles": ["coordinator", "auditor"],
    "redis_url": "redis://127.0.0.1:${REDIS_PORT}/0"
  },
  "worker": {
    "repo_roots": ["${HOME}"]
  },
  "limits": {
    "lease_ttl_seconds": 1800,
    "heartbeat_interval_seconds": 15
  },
  "notifications": {"desktop": false, "pubsub": true},
  "paths": {
    "repo_cache_root": "${HOME}/.prforge/repos",
    "worktree_root": "${HOME}/.prforge/worktrees",
    "quarantine_root": "${HOME}/.prforge/quarantine",
    "checkout_meta_root": "${MESH_DIR}/checkouts"
  },
  "max_workers": 3
}
EOF

# ── stop old coordinator if running ──────────────────────────────────────────
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID"
    echo "Stopped previous coordinator (PID $OLD_PID)"
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

# ── start daemon ─────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
nohup python3 prforge_mesh.py --config "$MESH_DIR/config.json" coordinator \
  > "$LOG" 2>&1 &
DAEMON_PID=$!
echo "$DAEMON_PID" > "$PID_FILE"
sleep 2

if kill -0 "$DAEMON_PID" 2>/dev/null; then
  echo "✓ coordinator started  PID=$DAEMON_PID  redis=127.0.0.1:$REDIS_PORT"
  echo "  log: $LOG"
  tail -5 "$LOG"
else
  echo "ERROR: coordinator daemon died at startup"
  cat "$LOG"
  exit 1
fi
