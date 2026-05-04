---
name: pr-distributed
description: "Configure and control PRForge LAN Mesh (watchtower/forge)."
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# /pr-distributed — PRForge LAN Mesh Setup and Control

You are executing a PRForge LAN Mesh command.
Follow these instructions exactly. Use your Write and Bash tools to create real files on disk.

## Parse the argument

The user typed `/pr-distributed <arg>` or `/pr-distributed <arg> <host> <code>`.
The arg is one of:
`watchtower` | `forge` | `status` | `off`

For `forge`, optional inline args:
- `/pr-distributed forge` — will prompt for host + code
- `/pr-distributed forge 192.168.1.20` — host provided, will prompt for code
- `/pr-distributed forge 192.168.1.20 7KQ4-MESH` — all provided inline

## Locate mesh scripts

```bash
MESH_SCRIPTS=$(find "$HOME" -path "*/prforge/*/scripts/mesh" -type d 2>/dev/null | head -1)
if [ -z "$MESH_SCRIPTS" ]; then
  echo "PRForge mesh scripts not found. Is the plugin installed?"
  exit 1
fi
```

---

## ACTION: `watchtower`

Sets up THIS machine as the LAN boss (coordinator + auditor + manager).

1. Check prerequisites:
   ```bash
   python3 --version
   python3 -c "import redis; print(redis.__version__)" 2>/dev/null || echo "REDIS_MISSING"
   gh auth status 2>&1 | head -3
   ```

   If redis-py is missing: `pip install redis>=4.6.0`
   If `gh` auth fails: warn "gh auth login required for watchtower auditing"

2. Generate join secret:
   ```bash
   python3 "$MESH_SCRIPTS/meshctl.py" setup --mode lan --role watchtower
   ```

   The command will:
   - Create `~/.prforge-mesh/lan/watchtower/config.json`
   - Generate `~/.prforge-mesh/lan/watchtower/mesh-secret`
   - Auto-detect Redis port (default 6386, tries next if taken)
   - Generate `~/.prforge-mesh/redis/redis-lan.conf` with auth
   - Start PRForge Redis with auth
   - Generate + start `prforge-lan-watchtower.service`
   - Write session pointer to `~/.prforge-mesh/sessions/lan/<session_id>`
   - Print: "✓ watchtower online — managing + auditing"
   - Print join details (Host + Code)

3. If the setup command fails, report the error. Do NOT suggest manual systemctl commands.

---

## ACTION: `forge`

Sets up THIS machine as a LAN worker (editing agent).

### Inline args check:
- If `<host>` provided: use it, skip host prompt
- If `<code>` provided: use it, skip code prompt
- If either missing: prompt once, then save

### Step 1: Get watchtower host + code

```bash
# Check for saved values first
HOST_FILE="$HOME/.prforge-mesh/lan/watchtower-host"
SECRET_FILE="$HOME/.prforge-mesh/lan/watchtower-secret"

if [ -n "$ARG_HOST" ]; then
  WATCHTOWER_HOST="$ARG_HOST"
elif [ -f "$HOST_FILE" ]; then
  WATCHTOWER_HOST=$(cat "$HOST_FILE")
else
  read -p "Watchtower hostname or IP: " WATCHTOWER_HOST
fi

if [ -n "$ARG_CODE" ]; then
  JOIN_CODE="$ARG_CODE"
elif [ -f "$SECRET_FILE" ]; then
  JOIN_CODE=$(cat "$SECRET_FILE")
else
  read -p "Join code: " JOIN_CODE
fi
```

Save for next time:
```bash
echo "$WATCHTOWER_HOST" > "$HOST_FILE"
echo "$JOIN_CODE" > "$SECRET_FILE"
```

### Step 2: Run meshctl setup

```bash
python3 "$MESH_SCRIPTS/meshctl.py" setup --mode lan --role forge \
  --host "$WATCHTOWER_HOST" --code "$JOIN_CODE"
```

The command will:
- Create `~/.prforge-mesh/lan/forge-<hostname>/config.json`
- Auto-detect Redis port from watchtower (default 6386)
- Generate + start `prforge-lan-forge.service`
- Write session pointer to `~/.prforge-mesh/sessions/lan/<session_id>`
- Print: "✓ forge online — connected to watchtower at <host>"
  (or "✓ forge online — waiting for watchtower at <host>" if not reachable)

If the setup command fails, report the error.

---

## ACTION: `status`

Show the LAN mesh status in plain English.

```bash
python3 "$MESH_SCRIPTS/meshctl.py" status --mode lan
```

Example output:
```
PRForge LAN Mesh

  watchtower   online    managing + auditing
  forge-popos   online    idle
  forge-thinkpad  online    working on review feedback
```

If no nodes configured, print: "No LAN mesh nodes configured. Run /pr-distributed watchtower to start."

---

## ACTION: `off`

Stop ONLY this machine's node (the one attached to this Claude session).

```bash
python3 "$MESH_SCRIPTS/meshctl.py" stop --mode lan
```

The command will:
- Read the session pointer for this Claude instance
- Stop only that node's service
- Release that node's leases
- Print: "✓ <node_id> stopped"

If no session pointer found, print: "No active node found for this session."

---

## Guards

- Never suggest running `systemctl` commands manually — meshctl owns that
- Never mention coordinator/auditor/worker in user-facing output
- Never expose Redis URLs, ports, or config paths to the user
- Join code is shown once by watchtower, then saved — only prompt if missing
- `off` stops ONLY the current node, never the whole mesh
- If forge can't reach watchtower, report "waiting for watchtower" — don't hard-fail

---

## Verification (after implementation)

```bash
# Machine 1 (watchtower):
/pr-distributed watchtower
# → "✓ watchtower online — managing + auditing"
# → "Join forges with: Host: 192.168.1.20  Code: 7KQ4-MESH"

# Machine 2 (forge):
/pr-distributed forge 192.168.1.20 7KQ4-MESH
# → "✓ forge online — connected to watchtower at 192.168.1.20"

# Any machine:
/pr-distributed status
# → watchtower   online    managing + auditing
# → forge-popos   online    idle
```
