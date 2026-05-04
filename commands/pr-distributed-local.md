---
name: pr-distributed-local
description: "Configure and control PRForge Local Mesh (watchtower/forge)."
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# /pr-distributed-local — PRForge Local Mesh Setup and Control

You are executing a PRForge Local Mesh command.
This runs 3 Claude Code instances on the SAME machine.

Follow these instructions exactly. Use your Write and Bash tools to create real files on disk.

## Parse the argument

The user typed `/pr-distributed-local <arg>`. The arg is one of:
`watchtower` | `forge` | `status` | `off`

---

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

Sets up THIS Claude instance as the local boss (managing + auditing).

1. Check prerequisites:
   ```bash
   python3 --version
   python3 -c "import redis; print(redis.__version__)" 2>/dev/null || echo "REDIS_MISSING"
   gh auth status 2>&1 | head -3
   ```

   If redis-py is missing: `pip install redis>=4.6.0`
   If `gh` auth fails: warn "gh auth login required for watchtower auditing"

2. Run meshctl setup:
   ```bash
   python3 "$MESH_SCRIPTS/meshctl.py" setup --mode local --role watchtower
   ```

   The command will:
   - Create `~/.prforge-mesh/local/watchtower/config.json`
   - Auto-detect Redis port (default 6385, tries 6386/6387 if taken)
   - Generate `~/.prforge-mesh/redis/redis-local.conf`
   - Start PRForge Redis (NOT system Redis)
   - Generate + start `prforge-local-watchtower.service`
   - Write session pointer to `~/.prforge-mesh/sessions/local/<session_id>`
   - Print: "✓ watchtower online — managing + auditing"

3. If the setup command fails, report the error. Do NOT suggest manual systemctl commands.

---

## ACTION: `forge`

Sets up THIS Claude instance as a local worker (editing agent).

### Step 1: Run meshctl setup

```bash
python3 "$MESH_SCRIPTS/meshctl.py" setup --mode local --role forge
```

The command will:
- Acquire `~/.prforge-mesh/local/.assign.lock`
- Scan existing forge nodes, pick next: forge-1, forge-2, forge-3...
- Create `~/.prforge-mesh/local/forge-N/config.json`
  - Redis: 127.0.0.1:<auto-detected-port>
  - node_id: forge-N
  - Auto-detect repo roots
- Generate + start `prforge-local-forge-N.service`
- Write session pointer to `~/.prforge-mesh/sessions/local/<session_id>`
- Release lock
- If watchtower not visible: "✓ forge-N online — waiting for local watchtower"
- Else: "✓ forge-N online — connected to local watchtower"

### Step 2: If the setup command fails

Report the error. Do NOT suggest manual systemctl commands.

---

## ACTION: `status`

Show the local mesh status in plain English.

```bash
python3 "$MESH_SCRIPTS/meshctl.py" status --mode local
```

Example output:
```
PRForge Local Mesh

  watchtower   online    managing + auditing
  forge-1      online    idle
  forge-2      online    working on review feedback
```

If no nodes configured, print: "No local mesh nodes configured. Run /pr-distributed-local watchtower to start."

---

## ACTION: `off`

Stop ONLY this Claude instance's node.

```bash
python3 "$MESH_SCRIPTS/meshctl.py" stop --mode local
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
- Forge waits for watchtower gracefully — no hard-fail
- `off` stops ONLY the current node, never the whole mesh
- If forge can't reach watchtower, report "waiting for local watchtower"
- Do not touch system Redis on port 6379

---

## Verification (after implementation)

```bash
# Terminal 1:
/pr-distributed-local watchtower
# → "✓ watchtower online — managing + auditing"

# Terminal 2:
/pr-distributed-local forge
# → "✓ forge-1 online — connected to local watchtower"

# Terminal 3:
/pr-distributed-local forge
# → "✓ forge-2 online — connected to local watchtower"

/pr-distributed-local status
# → watchtower   online    managing + auditing
# → forge-1      online    idle
# → forge-2      online    idle

/pr-distributed-local off  # on forge terminals
# → "✓ forge-1 stopped"
```

---

## Summary of user-facing commands

```
/pr-distributed-local watchtower
/pr-distributed-local forge
/pr-distributed-local status
/pr-distributed-local off
```

That's it. No flags. No exposed internals.
