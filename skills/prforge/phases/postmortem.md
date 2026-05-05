# POSTMORTEM Phase

## Overview
This phase runs after a PR has reached a terminal state (MERGED/CLOSED/ABANDONED/REVERTED) to analyze the full lifecycle and extract learnings.

## Preconditions
- `terminal_snapshot.py` has already run
- `state.json` contains `outcome` field set to one of: `MERGED`, `CLOSED`, `ABANDONED`, `REVERTED`
- Phase is triggered only when PR reaches a terminal state

## Steps

### 1. Verify Terminal Snapshot
Confirm that `state.json` has the required fields:
- `outcome` — must be set to a terminal state
- `run_id` — unique run identifier
- `repo` — repository identifier

### 2. Read Artifacts
The following artifacts should be read from `.prforge/runs/<run_id>/`:
- `state.json` — current run state and metadata
- `github/pr.json` — PR details from GitHub API
- `review-comments.jsonl` — review comments stream
- `ci-runs.jsonl` — CI run results
- `git/final.diff` — final diff of changes
- `commits.jsonl` — commit history
- `contract.md` — PR specification contract
- `validation_ledger.md` — validation results
- `review_decomposition.md` — decomposed review steps

### 3. Generate Postmortem
Execute the postmortem generator:

```bash
python3 $PRFORGE_HOME/scripts/postmortem_generator.py generate \
  --run-dir .prforge/runs/<run_id> \
  --output .prforge/runs/<run_id>/postmortem.json
```

The generator will:
- Parse all artifacts to build a comprehensive summary
- Categorize outcomes into: what_was_done, could_be_better, avoid_next_time
- Extract maintainer preferences from review comments and PR interactions
- Identify evidence (review comments, CI runs, commits, diff stats)
- Assign confidence level (low/medium/high) based on data quality
- Tag appropriately for categorization

### 4. Update State Metadata
Update ONLY the metadata section of state (do NOT change phase):

```json
{
  "postmortem": {
    "generated": true,
    "file": ".prforge/runs/<run_id>/postmortem.json",
    "confidence": "<low|medium|high>",
    "generated_at": "<ISO 8601 timestamp>"
  }
}
```

Do NOT update:
- `phase` — remains as POSTMORTEM
- `status` — should remain unchanged
- Any other top-level fields

### 5. DoD Checklist (Exit Gate)
Before exiting POSTMORTEM phase, verify:

- [ ] `terminal_snapshot.py` has run and outcome is set
- [ ] All required artifacts are present in `.prforge/runs/<run_id>/`
- [ ] `postmortem.json` has been generated and is valid JSON
- [ ] `postmortem.json` conforms to `schemas/postmortem-schema.json`
- [ ] State metadata updated with postmortem fields (generated, file, confidence)
- [ ] Phase remains POSTMORTEM (no phase change)
- [ ] Postmortem contains: outcome, summary (4 arrays), evidence (typed refs), tags, confidence
- [ ] Evidence references are properly typed (review_comment, ci_run, commit, diff_stats)
- [ ] Maintainer preferences include: preference, inferred, evidence fields

## Success Criteria
- Postmortem file is generated and schema-valid
- State metadata is updated without changing phase
- All DoD checklist items pass

## Failure Handling
If generation fails:
1. Log error to state.metadata.postmortem.error
2. Set state.metadata.postmortem.generated = false
3. Include error details and which step failed
4. Still exit POSTMORTEM phase (failure is logged but phase completes)

## Output Files
- `.prforge/runs/<run_id>/postmortem.json` — Postmortem analysis
