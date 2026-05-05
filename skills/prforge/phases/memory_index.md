# MEMORY_INDEX Phase

## Overview
This phase runs after POSTMORTEM to index learnings into the memory ledger and rebuild full-text search indices.

## Preconditions
- POSTMORTEM phase is complete
- `postmortem.json` exists in `.prforge/runs/<run_id>/`
- Phase is triggered only after postmortem generation

## Steps

### 1. Read Postmortem
Load the generated postmortem file:

```bash
cat .prforge/runs/<run_id>/postmortem.json
```

Verify it conforms to `schemas/postmortem-schema.json`.

### 2. Index Memory Records
Execute the memory indexer:

```bash
python3 $PRFORGE_HOME/scripts/memory_indexer.py index \
  --postmortem .prforge/runs/<run_id>/postmortem.json \
  --run-dir .prforge/runs/<run_id>
```

The indexer will:
- Extract lessons from postmortem summary arrays
- Create memory records for each lesson type (what_worked, could_be_better, avoid_next_time, maintainer_preferences)
- Compute lesson_fingerprint for de-duplication
- Determine scope (repo, subsystem, file_globs, maintainer) from evidence
- Set initial promotion_state (candidate) and confidence
- Link evidence_refs to postmortem and artifacts
- Insert into memory_records table
- Increment recurrence_count for duplicate lessons

### 3. Rebuild FTS
Rebuild the full-text search index for memory records:

```bash
python3 $PRFORGE_HOME/scripts/memory_ledger.py rebuild-fts
```

This will:
- Drop existing `memory_fts` virtual table if present
- Recreate FTS5 virtual table
- Populate from all active memory_records
- Index: lesson_text, subsystem, repo_scope, lesson_type, maintainer

### 4. Update State Metadata
Update ONLY the metadata section of state (do NOT change phase):

```json
{
  "postmortem": {
    "indexed": true
  },
  "memory_context": {
    "indexed_at": "<ISO 8601 timestamp>",
    "memory_count": <number of memory records added>
  }
}
```

Do NOT update:
- `phase` — remains as MEMORY_INDEX
- `status` — should remain unchanged
- Any other top-level fields

### 5. Handle No-Memory Path
If `memory_count = 0` (no records indexed):
- Still mark `postmortem.indexed = true`
- Set `memory_context.memory_count = 0`
- Do NOT fail the phase — this is valid (no learnings to extract)
- Log informational message: "No new memory records extracted"

### 6. DoD Checklist (Exit Gate)
Before exiting MEMORY_INDEX phase, verify:

- [ ] Postmortem file exists and is valid JSON
- [ ] Memory indexer ran successfully
- [ ] Memory records inserted into database (or 0 if none)
- [ ] FTS rebuilt successfully (`memory_fts` table populated)
- [ ] State metadata updated with indexed=true and memory_count
- [ ] Phase remains MEMORY_INDEX (no phase change)
- [ ] All lesson_fingerprints are unique in the run
- [ ] recurrence_count increments for existing fingerprints
- [ ] Evidence references correctly link to postmortem/artifacts
- [ ] promotion_state defaults to 'candidate' for new records

## Success Criteria
- Memory records indexed into database
- FTS rebuilt and queryable
- State metadata updated without changing phase
- All DoD checklist items pass

## Failure Handling
If indexing fails:
1. Log error to state.metadata.memory_context.error
2. Set state.memory_context.indexed = false
3. Include error details and which step failed
4. Still exit MEMORY_INDEX phase (failure is logged but phase completes)
5. Do NOT leave partial FTS state — rollback or document

## Output
- Database: `memory_ledger.db` — updated memory_records and memory_fts
- State: metadata.postmortem.indexed, metadata.memory_context updated

## Query Examples
After indexing, memory can be queried:

```sql
-- Find active repo-scoped lessons
SELECT * FROM memory_records 
WHERE promotion_state = 'active' 
  AND repo_scope = 'org/repo';

-- FTS search for lessons about testing
SELECT * FROM memory_fts 
WHERE memory_fts MATCH 'test';
```
