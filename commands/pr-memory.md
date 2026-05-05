---
name: pr-memory
description: "PRForge memory operations — view, audit, search, and manage PR lifecycle memory."
---

# /pr-memory — PRForge Memory Operations

Manage the PR lifecycle memory system. The memory system learns from every PR cycle and converts reviewer feedback + merge outcomes into durable, scoped, evidence-backed engineering memory.

## Usage

```
/pr-memory <action> [options]
```

Actions: `status` | `search` | `audit` | `postmortem` | `index` | `recall`

---

## Action: `status`

Show memory system status.

```bash
python3 $PRFORGE_HOME/scripts/memory_ledger.py stats
```

Display:
- Total runs tracked
- Artifacts registered
- Events logged
- Postmortems generated
- Memory records (total, active, candidate)
- Database path

---

## Action: `search`

Search memory for relevant lessons.

```bash
python3 $PRFORGE_HOME/scripts/memory_indexer.py query \
  --query "<search terms>" \
  --repo "<org/repo>" \
  --limit 10
```

Results are ranked by scope:
1. Exact repo + matching files/subsystem
2. Exact repo + matching change type
3. Same org/ecosystem + matching subsystem
4. Global active lessons (recurrence_count >= 2)

Each result shows: lesson, scope, type, confidence, recurrence count, evidence refs.

---

## Action: `audit`

Run memory quality audit.

```bash
python3 $PRFORGE_HOME/scripts/memory_audit.py audit \
  --min-confidence medium \
  --format text
```

Checks for:
- Low-confidence records promoted to active
- Inferred preferences with recurrence_count < 2 promoted to active
- Scope creep (universal claims from single-PR evidence)
- Orphaned records (no matching postmortem/artifacts)
- Records with missing evidence artifacts

---

## Action: `postmortem`

View or regenerate a postmortem for a specific run.

```bash
# View existing postmortem
cat .prforge/runs/<run_id>/postmortem.json

# Regenerate from artifacts
python3 $PRFORGE_HOME/scripts/postmortem_generator.py generate \
  --run-dir .prforge/runs/<run_id> \
  --output .prforge/runs/<run_id>/postmortem.json
```

---

## Action: `index`

Re-index memories from postmortem.

```bash
python3 $PRFORGE_HOME/scripts/memory_indexer.py index \
  --postmortem .prforge/runs/<run_id>/postmortem.json \
  --run-dir .prforge/runs/<run_id>
```

This:
1. Verifies all evidence artifacts exist and match stored hashes
2. Extracts lessons with scoped evidence references
3. Deduplicates via lesson_fingerprint
4. Promotes recurring lessons to active
5. Rebuilds FTS index

---

## Action: `recall`

Show all memories relevant to a specific repo/context.

```bash
python3 $PRFORGE_HOME/scripts/preflight_injector.py inject \
  --repo <org/repo> \
  --files "<file_patterns>" \
  --issue-type <bug|feature|docs|test|refactor> \
  --limit 20
```

This is the same query used at INTAKE phase. Useful for reviewing what PRForge has learned about a specific codebase before starting new work.

---

## Memory Architecture

```
Layer 0 — Raw artifacts (CANONICAL TRUTH)
  .prforge/runs/<run_id>/github/, git/, validation/, agent/

Layer 1 — SQLite ledger (manifest over artifacts)
  ~/.prforge/prforge_memory.db
  Tables: runs, artifacts, pr_events, postmortems, memory_records

Layer 2 — Derived postmortem
  .prforge/runs/<run_id>/postmortem.json

Layer 3 — Memory records
  Scoped lessons with evidence_artifact_ids

Layer 4 — FTS retrieval
  Rebuildable index for preflight queries
```

Key rules:
- SQLite is the ledger/index, NOT the canonical truth
- No evidence = no memory promotion
- Every memory needs scope (repo, subsystem, file_globs)
- Inferred preferences require recurrence_count >= 2 to promote
- FTS is rebuildable from SQLite — never the only copy
