---
name: pr-mesh-status
description: "Check the comprehensive health status of the PRForge mesh."
allowed-tools: Read, Bash
---

# /pr-mesh-status — Mesh Health Check

This command provides a comprehensive overview of the PRForge mesh infrastructure.

## Execution
Run the following checks to verify mesh status:

```bash
echo "=== PRForge Mesh Health Check ==="
echo "[1] Checking Redis Service..."
if systemctl --user is-active --quiet prforge-redis-tunnel.service 2>/dev/null; then
  echo "    Redis Tunnel: ACTIVE"
else
  echo "    Redis Tunnel: INACTIVE (or not configured for this node)"
fi

echo "[2] Checking Worker Service..."
if systemctl --user is-active --quiet prforge-worker.service 2>/dev/null; then
  echo "    Worker: ACTIVE"
else
  echo "    Worker: INACTIVE"
fi

echo "[3] Checking Coordinator Service..."
if systemctl --user is-active --quiet prforge-coordinator.service 2>/dev/null; then
  echo "    Coordinator: ACTIVE"
else
  echo "    Coordinator: INACTIVE"
fi

echo "[4] Checking Auditor Service..."
if systemctl --user is-active --quiet prforge-auditor.service 2>/dev/null; then
  echo "    Auditor: ACTIVE"
else
  echo "    Auditor: INACTIVE"
fi

echo ""
echo "[5] Running prforge_mesh.py status..."
SKILL_ROOT=$(find "$HOME" -path "*/prforge/*/skills/prforge" -type d 2>/dev/null | head -1)
MESH_SCRIPTS=$(find "$HOME" -path "*/prforge/*/scripts/mesh" -type d 2>/dev/null | head -1)
if [ -n "$MESH_SCRIPTS" ]; then
  python3 "$MESH_SCRIPTS/prforge_mesh.py" status || true
else
  echo "    Mesh scripts not found."
fi
```