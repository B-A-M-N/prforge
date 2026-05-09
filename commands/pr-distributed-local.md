---
name: pr-distributed-local
description: "Configure and control PRForge Local Mesh — vertical scaling on ONE machine."
allowed-tools: Bash
---

# /pr-distributed-local

The arg is one of: `coordinator` | `worker` | `status` | `off`

Find the mesh scripts directory, then run the corresponding script. Do all of this in a single Bash call.

## `coordinator`

```bash
MESH_SCRIPTS=$(python3 -c "
from pathlib import Path
hits = [p for p in Path.home().rglob('prforge_mesh.py') if '.git' not in str(p) and 'plugins' not in str(p)]
print(hits[0].parent if hits else 'NOT_FOUND')
") && [ "$MESH_SCRIPTS" != "NOT_FOUND" ] && bash "$MESH_SCRIPTS/start-coordinator.sh"
```

## `worker`

```bash
MESH_SCRIPTS=$(python3 -c "
from pathlib import Path
hits = [p for p in Path.home().rglob('prforge_mesh.py') if '.git' not in str(p) and 'plugins' not in str(p)]
print(hits[0].parent if hits else 'NOT_FOUND')
") && [ "$MESH_SCRIPTS" != "NOT_FOUND" ] && bash "$MESH_SCRIPTS/start-worker.sh"
```

## `status`

```bash
MESH_SCRIPTS=$(python3 -c "
from pathlib import Path
hits = [p for p in Path.home().rglob('prforge_mesh.py') if '.git' not in str(p) and 'plugins' not in str(p)]
print(hits[0].parent if hits else 'NOT_FOUND')
") && [ "$MESH_SCRIPTS" != "NOT_FOUND" ] && bash "$MESH_SCRIPTS/mesh-status.sh"
```

## `off`

```bash
MESH_SCRIPTS=$(python3 -c "
from pathlib import Path
hits = [p for p in Path.home().rglob('prforge_mesh.py') if '.git' not in str(p) and 'plugins' not in str(p)]
print(hits[0].parent if hits else 'NOT_FOUND')
") && [ "$MESH_SCRIPTS" != "NOT_FOUND" ] && bash "$MESH_SCRIPTS/mesh-off.sh"
```
