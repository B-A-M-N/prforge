#!/usr/bin/env bash
# PRForge Mesh — systemd user service generator
# Run after /pr-distributed <role> has written ~/.prforge-mesh/config.json

set -euo pipefail

MESH_CONFIG="$HOME/.prforge-mesh/config.json"
MESH_ENV="$HOME/.prforge-mesh/mesh.env"
SYSTEMD_DIR="$HOME/.config/systemd/user"
SERVICES_DIR="$HOME/.prforge-mesh/services"

# Locate mesh scripts directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MESH_SCRIPTS="$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Prereqs
# ---------------------------------------------------------------------------

if [[ ! -f "$MESH_CONFIG" ]]; then
    echo "ERROR: $MESH_CONFIG not found. Run /pr-distributed <role> first."
    exit 1
fi

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.8+."
    exit 1
fi

if ! python3 -c "import redis" 2>/dev/null; then
    echo "ERROR: redis-py not installed. Run: pip3 install redis"
    exit 1
fi

mkdir -p "$SYSTEMD_DIR" "$SERVICES_DIR"

# ---------------------------------------------------------------------------
# Read config
# ---------------------------------------------------------------------------

ROLES=$(python3 -c "
import json, sys
c = json.load(open('$MESH_CONFIG'))
print(','.join(c['mesh']['roles']))
")

NODE_ID=$(python3 -c "
import json
c = json.load(open('$MESH_CONFIG'))
print(c['mesh']['node_id'])
")

REDIS_URL=$(python3 -c "
import json
c = json.load(open('$MESH_CONFIG'))
print(c['mesh']['redis_url'])
")

echo "Installing services for node=$NODE_ID roles=$ROLES"

# ---------------------------------------------------------------------------
# Write mesh.env
# ---------------------------------------------------------------------------

cat > "$MESH_ENV" <<EOF
PRFORGE_MESH_REDIS=${REDIS_URL}
PRFORGE_MESH_NODE_ID=${NODE_ID}
PRFORGE_MESH_CLUSTER=$(python3 -c "import json; c=json.load(open('$MESH_CONFIG')); print(c['mesh']['cluster_name'])")
PRFORGE_MESH_ROLES=${ROLES}
EOF

echo "Written: $MESH_ENV"

# ---------------------------------------------------------------------------
# Service generators
# ---------------------------------------------------------------------------

write_worker_service() {
    local svc="$SYSTEMD_DIR/prforge-worker.service"
    cat > "$svc" <<EOF
[Unit]
Description=PRForge Mesh Worker ($NODE_ID)
After=network-online.target

[Service]
Type=simple
EnvironmentFile=%h/.prforge-mesh/mesh.env
WorkingDirectory=$MESH_SCRIPTS
ExecStart=/usr/bin/python3 $MESH_SCRIPTS/prforge_mesh.py worker
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
    cp "$svc" "$SERVICES_DIR/prforge-worker.service"
    echo "Written: $svc"
}

write_coordinator_service() {
    local svc="$SYSTEMD_DIR/prforge-coordinator.service"
    cat > "$svc" <<EOF
[Unit]
Description=PRForge Mesh Coordinator ($NODE_ID)
After=network-online.target redis.service

[Service]
Type=simple
EnvironmentFile=%h/.prforge-mesh/mesh.env
WorkingDirectory=$MESH_SCRIPTS
ExecStart=/usr/bin/python3 $MESH_SCRIPTS/prforge_mesh.py coordinator
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
    cp "$svc" "$SERVICES_DIR/prforge-coordinator.service"
    echo "Written: $svc"
}

write_auditor_service() {
    local svc="$SYSTEMD_DIR/prforge-auditor.service"
    cat > "$svc" <<EOF
[Unit]
Description=PRForge Mesh Auditor ($NODE_ID)
After=network-online.target

[Service]
Type=simple
EnvironmentFile=%h/.prforge-mesh/mesh.env
WorkingDirectory=$MESH_SCRIPTS
ExecStart=/usr/bin/python3 $MESH_SCRIPTS/prforge_mesh.py auditor
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
    cp "$svc" "$SERVICES_DIR/prforge-auditor.service"
    echo "Written: $svc"
}

write_tunnel_service() {
    local coordinator_host="${1:-machine3}"
    local ssh_user="${2:-$USER}"
    local local_port="${3:-6380}"
    local svc="$SYSTEMD_DIR/prforge-redis-tunnel.service"
    cat > "$svc" <<EOF
[Unit]
Description=PRForge Mesh Redis SSH Tunnel to $coordinator_host
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ssh -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L ${local_port}:127.0.0.1:6379 ${ssh_user}@${coordinator_host}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
    cp "$svc" "$SERVICES_DIR/prforge-redis-tunnel.service"
    echo "Written: $svc"
}

# ---------------------------------------------------------------------------
# Install based on roles
# ---------------------------------------------------------------------------

IFS=',' read -ra ROLE_ARR <<< "$ROLES"
for role in "${ROLE_ARR[@]}"; do
    case "$role" in
        worker)
            write_worker_service
            # If not on coordinator machine, write tunnel service
            if [[ -n "${PRFORGE_COORDINATOR_HOST:-}" ]]; then
                write_tunnel_service \
                    "${PRFORGE_COORDINATOR_HOST}" \
                    "${PRFORGE_SSH_USER:-$USER}" \
                    "${PRFORGE_LOCAL_REDIS_PORT:-6380}"
            else
                echo ""
                echo "NOTE: SSH tunnel service not written."
                echo "Set PRFORGE_COORDINATOR_HOST, PRFORGE_SSH_USER, PRFORGE_LOCAL_REDIS_PORT"
                echo "and re-run install_services.sh to generate prforge-redis-tunnel.service."
            fi
            ;;
        coordinator)
            write_coordinator_service
            ;;
        auditor)
            write_auditor_service
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Reload systemd
# ---------------------------------------------------------------------------

systemctl --user daemon-reload
echo ""
echo "Services installed. To enable:"
for role in "${ROLE_ARR[@]}"; do
    case "$role" in
        worker)
            [[ -f "$SYSTEMD_DIR/prforge-redis-tunnel.service" ]] && \
                echo "  systemctl --user enable --now prforge-redis-tunnel.service"
            echo "  systemctl --user enable --now prforge-worker.service"
            ;;
        coordinator)
            echo "  systemctl --user enable --now prforge-coordinator.service"
            ;;
        auditor)
            echo "  systemctl --user enable --now prforge-auditor.service"
            ;;
    esac
done
echo ""
echo "Status: /pr-distributed status"
