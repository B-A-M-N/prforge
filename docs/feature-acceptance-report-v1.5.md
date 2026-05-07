# PRForge v1.5 Feature Acceptance Report

Date: 2026-05-07
Branch: `fix/prforge-v1_5-stabilization`
Public actions: none executed

## Feature Acceptance Matrix

| Feature | Test command/path | Expected behavior | Actual behavior | Pass/Fail | Evidence | Fix needed |
|---|---|---|---|---|---|---|
| 1. Basic `/pr` intake | Temp repo probe using `hooks/prforge-common.sh`, `prforge_artifact_dir`, `prforge_ensure_pointer`, and `state.json` write | Initializes run state, records repo/remotes/branch, creates artifacts outside target repo, keeps target repo clean | Created `.prforge-run` pointer and outside run dir under `/tmp/.../home-prforge/runs/...`; `state.json` recorded origin/upstream/branch; `git status --porcelain` was empty | Pass | `artifact_dir=/tmp/prforge-intake.../home-prforge/runs/example__fork/master/...`; empty status output | None |
| 2. Phase lifecycle | `python3 scripts/validate_phase_machine.py`; `bash scripts/tests/hooks/test_prforge_regressions.sh` | Canonical lifecycle is consistent; illegal skips and missing artifacts are blocked | Phase definitions passed across SKILL, hooks, playbooks, README. Regression tests confirmed phase gate blocks early writes and validation evidence rejects unsupported claims | Pass | `PASS - Phase machine is consistent`; regression script passed | None |
| 3. Git safety | `bash scripts/tests/hooks/test_preflight.sh`; `scripts/pr_approve.py` checks | Blocks upstream push, `git push -u upstream`, raw force, public `gh` writes before approval; allows read-only git | All listed unsafe push/public paths blocked; read-only `git status` allowed; approved-preview verifier accepted only exact approved command/text | Pass | `All preflight regression tests passed`; approval verifier pass lines in regression script | None |
| 4. Mesh lock enforcement | `PYTHON=/usr/bin/python3 bash scripts/mesh/validate_mesh.sh`; `bash scripts/tests/hooks/test_prforge_regressions.sh` | Requires leases for writes; same-file conflicts/advisory path works; advisory jobs read-only; expired/stale leases block or requeue safely | Full mesh validator passed 41/41 after fixing stale acceptance fixtures to use v1.5 target leases and JSON lease payloads | Pass | `Results: PASS=41 FAIL=0 SKIP=0`; commit `be84f54` | None |
| 5. Validation evidence | `scripts/validation_evidence.py` via regression tests | Ledger requires captured command evidence; unrun tests cannot be claimed; approval fails if evidence missing/stale | Claimed `npm run unexecuted` was rejected; approval verifier requires `validation_ledger.md` hash and artifact presence | Pass | `validation evidence rejects unrun command claims`; `approval verifier... accepts approved push` | None |
| 6. Approval integrity | `scripts/pr_approve.py`; dogfood approval probe in `/tmp/prforge-dogfood-be84f54` | Captures exact public text, detects diff/hash changes, refuses stale/unapproved approval, executes no public action without approval | Unapproved public action verification failed; after simulated `approved=true`, exact approved PR body verified OK. No push/PR/comment executed | Pass | `FAIL: approval.approved=true...` then `OK`; `approval verifier enforces public PR body/comment previews` | None |
| 7. Monitors | `bash scripts/tests/hooks/test_prforge_regressions.sh`; `monitors/*.sh` | Local, worker, and coordinator watches start/stop cleanly, prevent duplicates, no unbounded hang | Local monitor duplicate prevention and PID cleanup passed; worker/coordinator one-shot lifecycle passed; shell syntax passed | Pass | `monitors prevent duplicates and support one-shot lifecycle`; `shell syntax` | None |
| 8. Memory | `scripts/memory_ledger.py`, `scripts/postmortem_generator.py`, `scripts/memory_indexer.py`, `scripts/preflight_injector.py` regression paths | Postmortem and memory ledger/index/recall work with scoped lessons | Ledger initialized, memory record inserted, preflight recall returned repo-scoped lesson. Postmortem/indexer code paths are present; acceptance directly proved ledger + recall | Partial | `memory preflight recalls ledger records`; postmortem/indexer not exercised end-to-end in one command during this pass | Add an end-to-end memory regression tying generated postmortem -> indexer -> recall |
| 9. Candidate discovery | `gh issue list --repo B-A-M-N/prforge ...`; `gh pr list --repo B-A-M-N/prforge ...`; `skills/prforge/modes/candidate_discovery.md` | Fetch candidate issues/PRs, score candidates, avoid claimed/large/high-risk work, produce ranked output | Read-only GitHub fetch path works with network approval, but this repo returned no open issues and no merged PRs, so ranking/exclusion output was not exercised. Scoring exists as mode instructions, not executable code | Partial | `gh issue list` returned `[]`; `gh pr list` returned `[]`; scoring rules in mode file | Add a deterministic candidate-discovery scorer/test fixture, or run against a non-empty target repo |
| 10. Real dogfood run | Local task: mesh validator fixture alignment; artifacts in `/tmp/prforge-dogfood-be84f54`; commit `be84f54` | Run through intake/package shape, verify artifacts, validation, diff scope, PR body, approval preview | Narrow local fix committed. Dogfood artifacts include contract, validation ledger, hostile review, DoD, PR body, approval preview. Approval verifier refused unapproved action and accepted exact approved preview after simulated approval | Pass | Commit `be84f54`; `PYTHON=/usr/bin/python3 bash scripts/mesh/validate_mesh.sh` pass 41/41; dogfood approval verifier outputs | None |

## Functional Status

### Proven Functional

- Basic intake artifact placement and repo cleanliness.
- Phase definition consistency and gate enforcement.
- Git/public-action safety gates.
- Mesh lock validator, duplicate/atomic lease behavior, stale worker reaper, manager mode gates.
- Validation evidence enforcement.
- Approval integrity and exact public text verification.
- Monitor lifecycle and duplicate prevention.
- Real local dogfood packaging through approval preview.

### Partially Functional

- Memory: scoped recall is proven; generated postmortem through indexer through recall should be covered by a single regression.
- Candidate discovery: GitHub fetch path is proven; scoring/ranking needs a non-empty repo or deterministic fixture because `B-A-M-N/prforge` returned no candidates.

### Not Functional

- None found after the validator fixture fix.

## Fix Commits Created

- `be84f54 test: align mesh validation with target leases`

## Remaining Blockers

- Add deterministic end-to-end tests for memory generation/indexing.
- Add executable candidate scoring coverage or run candidate discovery against a populated target repository.

## Dogfooding Readiness

PRForge v1.5 is ready for real dogfooding with caveats: use it for local and distributed runs where Redis and GitHub auth are available, and treat candidate discovery and memory-index learning as areas needing stronger regression coverage.
