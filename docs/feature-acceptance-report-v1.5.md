# prforge v1.5 feature acceptance report

date: 2026-05-07
branch: `fix/prforge-v1_5-stabilization`
public actions: none executed

## feature acceptance matrix

| feature | result | evidence/test | remaining caveat |
|---|---:|---|---|
| intake | pass | temp repo probe using `hooks/prforge-common.sh`, `prforge_artifact_dir`, `prforge_ensure_pointer`, and outside-repo `state.json`; target repo `git status --porcelain` stayed empty | none |
| phase lifecycle | pass | `python3 scripts/validate_phase_machine.py`; `bash scripts/tests/hooks/test_prforge_regressions.sh` | none |
| git safety | pass | `bash scripts/tests/hooks/test_preflight.sh`; approval verifier checks in `bash scripts/tests/hooks/test_prforge_regressions.sh` | none |
| mesh lock enforcement | pass | `PYTHON=/usr/bin/python3 bash scripts/mesh/validate_mesh.sh` returned `pass=41 fail=0 skip=0`; `python3 scripts/tests/mesh/test_mesh_redis_integration.py` | requires redis for full live mesh validation; in-memory integration test covers no-daemon path |
| validation evidence | pass | `scripts/validation_evidence.py` regression rejects unexecuted validation claims | none |
| approval integrity | pass | `scripts/pr_approve.py` rejects unapproved/stale/mismatched public text; dogfood preview artifacts in `/tmp/prforge-dogfood-be84f54` | public action execution intentionally not tested |
| monitors | pass | `bash scripts/tests/hooks/test_prforge_regressions.sh` covers duplicate prevention and one-shot monitor lifecycle; `bash -n monitors/*.sh` | persistent monitor behavior is bounded by shell lifecycle tests, not a long soak |
| memory indexing/recall | pass | `python3 scripts/tests/memory/test_memory_indexing_regression.py` | none |
| candidate discovery scoring | pass | `python3 scripts/tests/discovery/test_candidate_scoring_regression.py` | live github availability is not required by the scorer test |
| local dogfood package/approval-preview | pass | local task committed as `be84f54`; validation artifacts and approval preview verified by `scripts/pr_approve.py` | no public pr/comment/push executed |

## memory proof

- postmortem fixture path: created inline by `scripts/tests/memory/test_memory_indexing_regression.py` under a temporary run directory.
- indexer command/test: the test runs the actual `scripts/memory_indexer.py index --postmortem <tmp>/run/postmortem.json --run-dir <tmp>/run`.
- db isolation proof: the test sets `PRFORGE_MEMORY_DB=<tmp>/memory.db`, initializes the ledger with `scripts/memory_ledger.py init`, and never touches `~/.prforge`.
- ledger/fts proof: the test inserts a real run row and artifact through the ledger, indexes a high-confidence postmortem lesson, verifies one `memory_records` row, verifies `memory_fts match 'malformed'`, and verifies the expected lesson text.
- recall proof: the test runs `scripts/preflight_injector.py --repo example/prforge --files src/parser/tokenizer.py` and asserts the scoped lesson appears with `repo-scoped: example/prforge, subsystem src`.
- safety proof: the test verifies a missing-evidence lesson is skipped, malformed postmortem json fails, and repeated indexing updates the existing record to `recurrence_count=2` instead of creating a duplicate.

## candidate discovery proof

- scorer path: `scripts/candidate_discovery.py`.
- fixture/test path: `scripts/tests/discovery/test_candidate_scoring_regression.py`.
- ranking assertions: the fixture verifies a small, locally testable, maintainer-confirmed bug ranks first.
- downgrade assertions: the fixture verifies claimed/assigned, duplicate, large refactor-like, stale/no-maintainer-response, and auth/core-risk candidates receive penalties and rank below the best candidate.
- reason assertions: the fixture verifies scored output contains reasons such as `locally testable` and `maintainer confirmed`, plus penalties such as `claimed or assigned`, `duplicate/already covered`, and `high dependency/auth/core risk`.
- decision output assertions: the fixture verifies `risk_level`, `reason_summary`, `likely_files`, `subsystems`, `testability_signal`, `maintainer_signal`, `scope_size_signal`, `claimed_duplicate_stale_signal`, `suggested_next_action`, and `reject_reason`.
- empty-result behavior: the fixture verifies `[]` returns `{"status": "no_candidates", "candidates": []}` without crashing.

## product-standard audit

| area | intended behavior | current proof | gap | severity | fix plan |
|---|---|---|---|---:|---|
| lifecycle | phase is explicit, legal transitions enforced, repair path named | `scripts/validate_phase_machine.py`; hook regression suite | legacy docs still referred to `SHIPPED` as a state | p1 | fixed wording to canonical APPROVAL -> POSTMORTEM with terminal `outcome` |
| candidate discovery | rankings explain score, risk, scope, testability, maintainer status, likely files, and reject reason | `scripts/tests/discovery/test_candidate_scoring_regression.py` | scorer output was too thin for selection decisions | p1 | enriched scorer output and fixture assertions |
| repo investigation | repo intelligence artifact required before PLAN | phase-boundary checks and quality gates | no new gap found in this pass | p2 | leave; covered by existing acceptance |
| pr contract | allowed files, patch plan, dod, and validation commands required before edits | phase-boundary, policy engine, `validation_evidence.py` | no new gap found in this pass | p2 | leave; covered by existing acceptance |
| validation evidence | pr body cannot claim unrun commands | `scripts/validation_evidence.py`; hook regression suite | no new gap found in this pass | p2 | leave |
| approval integrity | exact public text and hashes checked before public action | `scripts/pr_approve.py`; hook regression suite | docs used stale shipped-time wording | p1 | fixed approval wording |
| git safety | upstream pushes and unsafe force pushes blocked | `scripts/tests/hooks/test_preflight.sh`; approval verifier regressions | no new gap found in this pass | p2 | leave |
| mesh locks | worker/coordinator/hook agree on target/path leases | mesh integration test and validator | docs and validator label still used legacy `lease:pr` wording | p1 | fixed docs and validator target lease wording |
| monitors | daemons start/stop, prevent duplicate processes, and use bounded loops | hook regression suite; `bash -n monitors/*.sh` | no new gap found in this pass | p2 | leave; no long soak claimed |
| memory | postmortem lessons are evidence-backed, scoped, indexed, and recalled | `scripts/tests/memory/test_memory_indexing_regression.py` | no new gap found in this pass | p2 | leave |
| hooks | inactive hooks quiet, no repo-local artifact pollution | hook smoke and artifact checks | no new gap found in this pass | p2 | leave |
| recovery states | blocked agents get reason and repair path | hook regression suite; quality gates | emergency override docs suggested direct phase mutation | p1 | fixed to BLOCKED + explicit override artifact |
| docs/ux | docs match actual artifact paths and safety model | acceptance report and dod | readme used repo-local `.prforge` state and destructive rollback wording | p1 | fixed narrowly |

## standards audit

| finding | severity | file | why it matters | fix or leave? | reason |
|---|---:|---|---|---|---|
| destructive rollback guidance suggested `git checkout .` and `git reset --hard` | p1 | `commands/pr-rollback.md` | could encourage loss of user work during rollback | fixed | replaced with status-first, preserve-user-work, quarantine, and ask-before-destructive guidance |
| candidate discovery lacked decision fields | p1 | `scripts/candidate_discovery.py` | output did not include enough context to choose or reject work confidently | fixed | added risk, summary, scope/testability/maintainer signals, inferred files/subsystems, suggested next action, and reject reason |
| legacy mesh target lock wording | p1 | `README.md`, `docs/MESH_IMPLEMENTATION.md`, `skills/prforge/roles/*`, `scripts/mesh/validate_mesh.sh` | docs/test labels could make hook/worker lease model look inconsistent | fixed | changed to target lease terminology |
| stale shipped/artifact-path wording | p1 | `README.md`, `references/quality-gates.md`, `skills/prforge/phases/shipped.md`, `skills/prforge/policies/approval-gate.md` | implied non-canonical phase or repo-local artifacts | fixed | clarified APPROVAL -> POSTMORTEM and outside-repo artifacts |
| bounded home scan guarded by env flag | p2 | `hooks/phase-injector.sh` | possible latency if explicitly enabled | leave | disabled by default and bounded |
| fake redis modules in tests | p2 | `scripts/mesh_test.py`, `scripts/tests/mesh/test_mesh_redis_integration.py` | wording matched audit grep | leave | intentional in-memory test double, not runtime fake gate |
| empty github marker artifacts when `gh` data is unavailable | p2 | `scripts/terminal_snapshot.py` | wording matched placeholder grep | leave | code records explicit metadata status instead of pretending data exists |

## full validation command list

```bash
git diff --check HEAD~1..HEAD || true
bash -n hooks/*.sh
bash -n monitors/*.sh
python3 -m py_compile \
  scripts/mesh/redis_backend.py \
  scripts/mesh/coordinator.py \
  scripts/mesh/worker.py \
  scripts/mesh/mesh_lock_guard.py \
  scripts/mesh/checkout_broker.py \
  scripts/mesh/meshctl.py \
  scripts/prforge_state.py \
  scripts/memory_ledger.py \
  scripts/memory_indexer.py \
  scripts/preflight_injector.py \
  scripts/pr_approve.py \
  scripts/validate_phase_machine.py \
  scripts/validation_evidence.py \
  scripts/candidate_discovery.py
bash scripts/tests/hooks/test_prforge_regressions.sh
bash scripts/tests/hooks/test_preflight.sh
python3 scripts/tests/mesh/test_mesh_redis_integration.py
python3 scripts/validate_phase_machine.py
python3 scripts/tests/memory/test_memory_indexing_regression.py
python3 scripts/tests/discovery/test_candidate_scoring_regression.py
time bash hooks/mesh-lock-guard.sh </dev/null || true
time bash hooks/preflight.sh </dev/null || true
time bash hooks/phase-gate-enforcer.sh </dev/null || true
time bash hooks/phase-injector.sh </dev/null || true
time bash hooks/memory-autocapture.sh </dev/null || true
time bash hooks/blast-radius.sh </dev/null || true
git status --short
find . -maxdepth 3 -type f \( -name 'hook_events.log' -o -name '.prforge-run' \) -print
find . -maxdepth 3 -type d -name '.prforge' -print
```

## commits created for this hardening pass

- `a6a1caf test(memory): add deterministic indexing recall regression`
- `65ab2b1 test(discovery): add deterministic candidate scoring fixture`
- `5b5dd89 docs(rollback): remove destructive default guidance`
- `5080498 fix(discovery): enrich candidate scoring output`

## remaining known limitations

- full mesh acceptance with `scripts/mesh/validate_mesh.sh` still requires a reachable redis instance and github auth for the live validator path.
- public push/pr/comment execution remains intentionally untested because acceptance rules prohibit public actions without explicit approval.

## readiness

prforge v1.5 can now honestly claim these are functional with executable proof:

- intake
- phase lifecycle
- git safety
- mesh lock enforcement
- validation evidence
- approval integrity
- monitors
- memory indexing and recall
- candidate discovery scoring
- local dogfood package and approval preview
