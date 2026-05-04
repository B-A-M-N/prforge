---
name: pr-distributed
description: "Configure and control PRForge Mesh distributed mode (worker/coordinator/auditor)."
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# /pr-distributed — PRForge Mesh Setup and Control

You are executing a PRForge Mesh configuration or control command.
Follow these instructions exactly. Use your Write and Bash tools to create real files on disk.

## Parse the argument

The user typed `/pr-distributed <arg>`. The arg is one of:
`worker` | `coordinator` | `auditor` | `coordinator,auditor` | `status` | `stop` | `resume` | `manager-mode <sub>`

Manager-mode sub: `off` | `certify-only` | `internal-actions` | `low-risk-public` | `status`

Locate mesh scripts:
```bash
SKILL_ROOT=$(find "$HOME" -path "*/prforge/*/skills/prforge" -type d 2>/dev/null | head -1)
MESH_SCRIPTS=$(find "$HOME" -path "*/prforge/*/scripts/mesh" -type d 2>/dev/null | head -1)
```

If MESH_SCRIPTS is empty, abort: "PRForge mesh scripts not found. Is the plugin installed?"

---

## ACTION: `status`

Run and display output:
```bash
python3 "$MESH_SCRIPTS/prforge_mesh.py" status
```

If it fails with "Config not found", tell the user to run `/pr-distributed worker` (or coordinator/auditor) first.

---

## ACTION: `stop`

Run:
```bash
systemctl --user stop prforge-worker.service 2>/dev/null || true
systemctl --user stop prforge-coordinator.service 2>/dev/null || true
systemctl --user stop prforge-auditor.service 2>/dev/null || true
python3 "$MESH_SCRIPTS/prforge_mesh.py" offline 2>/dev/null || true
```

Report which services were stopped.

---

## ACTION: `resume`

Run:
```bash
systemctl --user daemon-reload
systemctl --user start prforge-worker.service 2>/dev/null || true
systemctl --user start prforge-coordinator.service 2>/dev/null || true
systemctl --user start prforge-auditor.service 2>/dev/null || true
```

Report which services started.

---

## ACTION: `manager-mode <sub>`

Manage the Manager Mode policy layer. Only applies when distributed mode is active.

### Parse subcommand

The `<sub>` argument is one of:
- `off` — disable manager mode (standalone PRForge behavior preserved)
- `certify-only` — manager may certify/notify, may NOT execute public actions
- `internal-actions` — manager may requeue/block/revalidate/release leases/certify, may NOT execute public actions
- `low-risk-public` — manager may execute allowed_public_actions only, never forbidden_public_actions
- `status` — show current manager mode config

### For `status`

Read `~/.prforge-mesh/config.json`, print the `manager_mode` section.
If `manager_mode` is absent or `enabled` is false, print "Manager Mode: off".

### For `off` | `certify-only` | `internal-actions` | `low-risk-public`

1. Read existing `~/.prforge-mesh/config.json` (must exist — run setup first)
2. Update the `manager_mode` section:

For `off`:
```json
{
  "manager_mode": {
    "enabled": false,
    "authority": "off"
  }
}
```

For `certify-only`:
```json
{
  "manager_mode": {
    "enabled": true,
    "authority": "certify_only",
    "require_coordinator_pass": true,
    "require_auditor_pass": true,
    "require_clean_validation": true,
    "require_review_freshness": true,
    "require_ci_relatedness_clean": true,
    "require_no_unknown_ci_for_auto_ship": true,
    "require_no_scope_delta": true,
    "require_dod_evidence": true,
    "require_artifact_exclusion": true,
    "max_risk": "medium",
    "auto_requeue_on_fail": true,
    "auto_certify_on_pass": true,
    "auto_public_actions": false,
    "allowed_public_actions": [],
    "forbidden_public_actions": ["force_push", "merge", "delete_branch"]
  }
}
```

For `internal-actions`:
```json
{
  "manager_mode": {
    "enabled": true,
    "authority": "internal_actions",
    "require_coordinator_pass": true,
    "require_auditor_pass": true,
    "require_clean_validation": true,
    "require_review_freshness": true,
    "require_ci_relatedness_clean": true,
    "require_no_unknown_ci_for_auto_ship": true,
    "require_no_scope_delta": true,
    "require_dod_evidence": true,
    "require_artifact_exclusion": true,
    "max_risk": "medium",
    "auto_requeue_on_fail": true,
    "auto_certify_on_pass": true,
    "auto_public_actions": false,
    "allowed_public_actions": [],
    "forbidden_public_actions": ["force_push", "merge", "delete_branch"]
  }
}
```

For `low-risk-public`:
```json
{
  "manager_mode": {
    "enabled": true,
    "authority": "low_risk_public",
    "require_coordinator_pass": true,
    "require_auditor_pass": true,
    "require_clean_validation": true,
    "require_review_freshness": true,
    "require_ci_relatedness_clean": true,
    "require_no_unknown_ci_for_auto_ship": true,
    "require_no_scope_delta": true,
    "require_dod_evidence": true,
    "require_artifact_exclusion": true,
    "max_risk": "medium",
    "auto_requeue_on_fail": true,
    "auto_certify_on_pass": true,
    "auto_public_actions": false,
    "allowed_public_actions": ["push", "comment", "request_review"],
    "forbidden_public_actions": ["force_push", "merge", "delete_branch"]
  }
}
```

3. Write the updated config.json using your Write tool.
4. If `PRFORGE_MESH_SIGNING_KEY` is not set in the environment, warn the user:
   "⚠️ PRFORGE_MESH_SIGNING_KEY is not set. Manager Mode verdicts cannot be signed. Export it: export PRFORGE_MESH_SIGNING_KEY=<your-secret>"
5. Print the new manager mode status.

---

## ACTION: `worker` | `coordinator` | `auditor` | `coordinator,auditor`

You must:
1. Gather configuration values (interactive or from env)
2. Create `~/.prforge-mesh/config.json` using your Write tool
3. Create `~/.prforge-mesh/mesh.env` using your Write tool
4. Create systemd service files using your Write tool
5. Run daemon-reload
6. Print next steps

### Step 1 — Check prerequisites

```bash
python3 --version
python3 -c "import redis; print(redis.__version__)" 2>/dev/null || echo "REDIS_MISSING"
gh auth status 2>&1 | head -3
```

If redis-py is missing, run:
```bash
pip3 install redis>=4.6.0
pip3 install fastembed>=0.5.0
```
Then re-check.

If `gh` auth is failing AND role includes `auditor`, warn: "gh auth login required for auditor polling".

### Step 2 — Gather config values

Read from environment first, then prompt the user for anything missing:

For **worker** role:
- `PRFORGE_MESH_NODE_ID` or ask: "Node ID for this machine (e.g. worker-1):"
- `PRFORGE_MESH_CLUSTER` or default to: `bamn-prforge`
- `PRFORGE_MESH_REDIS` or ask: "Redis URL (e.g. redis://:PASSWORD@127.0.0.1:6380/0):"
- `PRFORGE_COORDINATOR_HOST` or ask: "Coordinator hostname/IP (Machine 3):"
- `PRFORGE_SSH_USER` or default to current user (`$USER`)
- `PRFORGE_LOCAL_REDIS_PORT` or default to `6380`
- Repo roots: ask "Paths to repo directories, comma-separated (e.g. /home/bamn/work):"

For **coordinator** or **auditor** or **coordinator,auditor** role:
- `PRFORGE_MESH_NODE_ID` or ask: "Node ID for this machine (e.g. machine3):"
- `PRFORGE_MESH_CLUSTER` or default to: `bamn-prforge`
- Redis URL: default to `redis://:PASSWORD@127.0.0.1:6379/0` (local Redis)
  Ask for the Redis password: "Redis password (from your redis.conf requirepass):"
  Construct URL: `redis://:PASSWORD@127.0.0.1:6379/0`

Parse PRFORGE_MESH_REDIS if set to extract host, port, password.

### Step 3 — Create directories

```bash
mkdir -p "$HOME/.prforge-mesh/logs" "$HOME/.prforge-mesh/services" "$HOME/.config/systemd/user"
```

### Step 4 — Write `~/.prforge-mesh/config.json`

Use your **Write tool** to create the file at the exact path `$HOME/.prforge-mesh/config.json`.

For **worker** config (substitute actual values gathered in Step 2):

```json
{
  "mesh": {
    "enabled": true,
    "redis_url": "REDIS_URL",
    "cluster_name": "CLUSTER_NAME",
    "node_id": "NODE_ID",
    "roles": ["worker"]
  },
  "limits": {
    "max_active_worker_jobs": 2,
    "max_jobs_per_worker": 1,
    "lease_ttl_seconds": 1800,
    "heartbeat_interval_seconds": 15
  },
  "worker": {
    "capacity": 1,
    "repo_roots": ["REPO_ROOT_1", "REPO_ROOT_2"],
    "auto_launch_claude": false,
    "launcher": "openclaude1",
    "allowed_modes": [
      "new_pr",
      "review_response",
      "pr_polish",
      "ci_fix_related_to_branch"
    ]
  },
  "auditor": { "enabled": false },
  "notifications": { "desktop": true, "pubsub": true },
  "manager_mode": {
    "enabled": false,
    "authority": "off",
    "require_coordinator_pass": true,
    "require_auditor_pass": true,
    "require_clean_validation": true,
    "require_review_freshness": true,
    "require_ci_relatedness_clean": true,
    "require_no_unknown_ci_for_auto_ship": true,
    "require_no_scope_delta": true,
    "require_dod_evidence": true,
    "require_artifact_exclusion": true,
    "max_risk": "medium",
    "auto_requeue_on_fail": true,
    "auto_certify_on_pass": true,
    "auto_public_actions": false,
    "allowed_public_actions": [],
    "forbidden_public_actions": ["force_push", "merge", "delete_branch"]
  }
}
```

For **coordinator,auditor** config:

```json
{
  "mesh": {
    "enabled": true,
    "redis_url": "REDIS_URL",
    "cluster_name": "CLUSTER_NAME",
    "node_id": "NODE_ID",
    "roles": ["coordinator", "auditor"]
  },
  "limits": {
    "max_active_worker_jobs": 2,
    "max_jobs_per_worker": 1,
    "lease_ttl_seconds": 1800,
    "heartbeat_interval_seconds": 15
  },
  "worker": {
    "capacity": 0,
    "repo_roots": [],
    "auto_launch_claude": false,
    "allowed_modes": []
  },
  "auditor": {
    "enabled": true,
    "lookback_days": 3,
    "poll_interval_minutes": 15,
    "audit_interval_minutes": 45,
    "skip_if_unchanged": true,
    "max_llm_audits_per_hour": 3,
    "queue_medium_findings_only_when_idle": true
  },
  "notifications": { "desktop": true, "pubsub": true },
  "manager_mode": {
    "enabled": false,
    "authority": "off",
    "require_coordinator_pass": true,
    "require_auditor_pass": true,
    "require_clean_validation": true,
    "require_review_freshness": true,
    "require_ci_relatedness_clean": true,
    "require_no_unknown_ci_for_auto_ship": true,
    "require_no_scope_delta": true,
    "require_dod_evidence": true,
    "require_artifact_exclusion": true,
    "max_risk": "medium",
    "auto_requeue_on_fail": true,
    "auto_certify_on_pass": true,
    "auto_public_actions": false,
    "allowed_public_actions": [],
    "forbidden_public_actions": ["force_push", "merge", "delete_branch"]
  }
}
```

For **coordinator** (only) config: same as coordinator,auditor but `"roles": ["coordinator"]` and `"auditor": {"enabled": false}`.

For **auditor** (only) config: same as coordinator,auditor but `"roles": ["auditor"]`.

### Step 5 — Write `~/.prforge-mesh/mesh.env`

Use your **Write tool** to create `$HOME/.prforge-mesh/mesh.env`:

```
PRFORGE_MESH_REDIS=REDIS_URL
PRFORGE_MESH_NODE_ID=NODE_ID
PRFORGE_MESH_CLUSTER=CLUSTER_NAME
PRFORGE_MESH_ROLES=ROLES_COMMA_SEPARATED
```

Substitute actual values.

### Step 6 — Write systemd service files

Use your **Write tool** for each service required by the roles.
All paths must be absolute (expand $HOME to actual home directory, e.g. /home/bamn).

**Worker service** (`~/.config/systemd/user/prforge-worker.service`):

```ini
[Unit]
Description=PRForge Mesh Worker (NODE_ID)
After=network-online.target

[Service]
Type=simple
EnvironmentFile=HOME_DIR/.prforge-mesh/mesh.env
WorkingDirectory=MESH_SCRIPTS_PATH
ExecStart=/usr/bin/python3 MESH_SCRIPTS_PATH/prforge_mesh.py worker
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

**SSH Tunnel service** (worker only — `~/.config/systemd/user/prforge-redis-tunnel.service`):

```ini
[Unit]
Description=PRForge Mesh Redis SSH Tunnel to COORDINATOR_HOST
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ssh -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L LOCAL_PORT:127.0.0.1:6379 SSH_USER@COORDINATOR_HOST
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

**Coordinator service** (`~/.config/systemd/user/prforge-coordinator.service`):

```ini
[Unit]
Description=PRForge Mesh Coordinator (NODE_ID)
After=network-online.target

[Service]
Type=simple
EnvironmentFile=HOME_DIR/.prforge-mesh/mesh.env
WorkingDirectory=MESH_SCRIPTS_PATH
ExecStart=/usr/bin/python3 MESH_SCRIPTS_PATH/prforge_mesh.py coordinator
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

**Auditor service** (`~/.config/systemd/user/prforge-auditor.service`):

```ini
[Unit]
Description=PRForge Mesh Auditor (NODE_ID)
After=network-online.target

[Service]
Type=simple
EnvironmentFile=HOME_DIR/.prforge-mesh/mesh.env
WorkingDirectory=MESH_SCRIPTS_PATH
ExecStart=/usr/bin/python3 MESH_SCRIPTS_PATH/prforge_mesh.py auditor
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

Substitute:
- `NODE_ID` — the actual node ID string
- `HOME_DIR` — the user's actual home path (e.g. /home/bamn)
- `MESH_SCRIPTS_PATH` — the resolved absolute path from Step 0
- `COORDINATOR_HOST`, `SSH_USER`, `LOCAL_PORT` — from Step 2 (worker only)

### Step 7 — Reload systemd and validate config

```bash
systemctl --user daemon-reload
python3 "$MESH_SCRIPTS/prforge_mesh.py" status 2>&1 | head -5 || echo "(Redis not yet reachable — start tunnel/Redis first)"
```

### Step 8 — Print summary

Print the following (with actual resolved values):

For **worker**:

```
PRForge Mesh worker configured.

Node: NODE_ID
Role: worker
Redis: REDIS_URL (via SSH tunnel on port LOCAL_PORT)
Repo roots:
  REPO_ROOTS

Created:
  ~/.prforge-mesh/config.json
  ~/.prforge-mesh/mesh.env
  ~/.config/systemd/user/prforge-worker.service
  ~/.config/systemd/user/prforge-redis-tunnel.service

Next:
  systemctl --user enable --now prforge-redis-tunnel.service
  systemctl --user enable --now prforge-worker.service

Status:
  /pr-distributed status
```

For **coordinator,auditor**:

```
PRForge Mesh coordinator/auditor configured.

Node: NODE_ID
Roles: coordinator,auditor
Redis: REDIS_URL (local)
Global worker job limit: 2
Auditor lookback: 3 days
Auditor poll interval: 15 minutes

Created:
  ~/.prforge-mesh/config.json
  ~/.prforge-mesh/mesh.env
  ~/.config/systemd/user/prforge-coordinator.service
  ~/.config/systemd/user/prforge-auditor.service

Redis must be local-only (verify /etc/redis/redis.conf):
  bind 127.0.0.1
  protected-mode yes
  requirepass <your-password>
  appendonly yes

Next:
  systemctl --user enable --now prforge-coordinator.service
  systemctl --user enable --now prforge-auditor.service

Status:
  /pr-distributed status
```

---

## Guards

- If config.json already exists: read it and show current values. Ask user to confirm overwrite before proceeding.
- If redis-py install fails: stop. User must fix manually before mesh can start.
- Never set `capacity > 0` on coordinator/auditor nodes.
- Never write coordinator/auditor roles on a worker node without explicit user confirmation.
- Do not create the SSH tunnel service if `PRFORGE_COORDINATOR_HOST` was not provided and role is `worker`. Warn: "SSH tunnel service not created — set coordinator host and re-run."
