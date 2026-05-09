---
name: pr-distributed-local
description: "Configure and control PRForge Local Mesh — vertical scaling on ONE machine."
allowed-tools: Bash
---

# /pr-distributed-local

The arg is one of: `coordinator` | `worker` | `status` | `off`

**Run the single bash command for the requested action. Nothing else.**

## `coordinator`

```bash
prforge-mesh coordinator
```

## `worker`

```bash
prforge-mesh worker
```

## `status`

```bash
prforge-mesh status
```

## `off`

```bash
prforge-mesh off
```
