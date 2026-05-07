# prforge v1.5 definition of done

date: 2026-05-07
branch: `fix/prforge-v1_5-stabilization`
scope: local and dogfood certification only. public push, pr creation, and comments are intentionally unexecuted unless explicitly approved.

## certification standard

prforge v1.5 is done only when each feature has executable proof, an artifact proving the behavior, or an explicit limitation. documentation alone is not proof.

| required feature | proof method | test command | artifact proving it | current status | limitations | next certification step |
|---|---|---|---|---|---|---|
| intake functional | temp repo/hook regression verifies run state, repo identity, artifacts, and clean target worktree | `bash scripts/tests/hooks/test_prforge_regressions.sh` | outside-repo run dir plus `.prforge-run` pointer check in regression | pass | none known | run against another local repo before public dogfood |
| phase lifecycle functional | canonical phase validator and hook regression verify legal transitions and blocked skips | `python3 scripts/validate_phase_machine.py` | phase transition table in `hooks/phase-boundary.sh` | pass | `skills/prforge/phases/shipped.md` remains legacy recovery-only documentation | remove legacy file after migration window if no runtime uses it |
| repo investigation functional | investigate-to-plan gate requires repo intelligence evidence or honest degraded fallback | `bash scripts/tests/hooks/test_prforge_regressions.sh` | `$ARTIFACT_DIR/repo_intelligence.md`; state intelligence evidence | pass | live gitnexus/context-mode availability is environment-dependent | add live gitnexus smoke when mcp is installed |
| pr contract functional | plan/implement gates require contract, patch plan, dod, allowed files, and plan compliance | `bash scripts/tests/hooks/test_prforge_regressions.sh` | `$ARTIFACT_DIR/contract.md`, `patch_plan.md`, `dod.md` | pass | complex multi-package contracts still rely on agent judgment for minimality | add a larger fixture repo contract test |
| validation evidence functional | ledger rejects fabricated/unrun validation and approval checks stale evidence | `bash scripts/tests/hooks/test_prforge_regressions.sh` | `$ARTIFACT_DIR/validation_ledger.md`; memory ledger command events | pass | cannot prove third-party ci unless ci is fetched live | add optional live ci fixture when public action testing is approved |
| approval integrity functional | exact public text, diff hash, validation hash, and stale approval rejection verified locally | `bash scripts/tests/hooks/test_prforge_regressions.sh` | `$ARTIFACT_DIR/approval.md`; `scripts/pr_approve.py` checks | pass | public command execution intentionally untested | run against a private sandbox repo with explicit approval |
| git safety functional | blocks upstream push, `git push -u upstream`, raw force, and ungated gh public actions | `bash scripts/tests/hooks/test_preflight.sh` | preflight and approval verifier regression output | pass | allowed `--force-with-lease` policy remains approval-gated and must be reviewed per case | add explicit force-with-lease fixture if policy changes |
| mesh lock enforcement functional | in-memory integration and redis validator cover job, target, branch, worker, path, stale, and advisory behavior | `python3 scripts/tests/mesh/test_mesh_redis_integration.py` | mesh lock/advisory assertions | pass | full redis validator requires reachable redis; no long-running lan soak claimed | run `PYTHON=/usr/bin/python3 bash scripts/mesh/validate_mesh.sh` in redis-enabled environment |
| monitors functional | shell syntax, lifecycle smoke, duplicate prevention, and bounded loop checks | `bash scripts/tests/hooks/test_prforge_regressions.sh`; `bash -n monitors/*.sh` | monitor pid/log assertions in regression | pass | no multi-hour daemon soak claimed | add soak test before unattended lan operation |
| hooks invisible when inactive | hook smoke verifies quiet no-op behavior and no repo-local artifacts | hook smoke commands in acceptance report | final `git status --short` and artifact find checks | pass | shell hook latency measured only by smoke timing | add latency budget regression if hooks grow |
| memory indexing/recall functional | deterministic postmortem fixture indexes into temp sqlite/fts and recall returns scoped lesson | `python3 scripts/tests/memory/test_memory_indexing_regression.py` | temp `memory.db`, `memory_records`, `memory_fts`, preflight recall output | pass | real future-run utility depends on postmortem quality | add recurrence/promotion tuning after more dogfood runs |
| candidate discovery scoring functional | deterministic fixture verifies ranking, rejection, signals, inferred files/subsystems, reasons, and empty set | `python3 scripts/tests/discovery/test_candidate_scoring_regression.py` | scorer json output from `scripts/candidate_discovery.py` | pass | live github fetching remains optional and environment-dependent | add live fetch smoke with gh auth against a sandbox repo |
| local dogfood package/approval preview functional | local task ran through package and approval preview without public action | `bash scripts/tests/hooks/test_prforge_regressions.sh` plus dogfood artifacts | `/tmp/prforge-dogfood-be84f54` approval preview | pass | public push/pr/comment not executed | perform private sandbox public-action certification with explicit approval |

## full validation set

```bash
git status --short
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
python3 scripts/tests/memory/test_memory_indexing_regression.py
python3 scripts/tests/discovery/test_candidate_scoring_regression.py
python3 scripts/validate_phase_machine.py
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

## known limitations

- public push, pr creation, review comments, issue comments, labels, and review requests are approval-gated and intentionally unexecuted in this certification.
- full live mesh validation requires redis and, for gh-specific checks, authenticated `gh`.
- monitor certification is lifecycle/smoke-level, not a multi-hour soak.
- gitnexus/context-mode intelligence is accepted through honest degraded fallback when unavailable.

## current decision

prforge v1.5 meets the intended product standard for local and dogfood use when operated under the approval gate. it is not certified for unattended public action execution.
