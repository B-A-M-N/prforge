# PRForge Certification Scorecard

**Last updated:** 2026-05-07
**Evaluator:** PRForge self-certification (dogfood runs on B-A-M-N/prforge)

---

## Scoring key

| Symbol | Meaning |
|---|---|
| ✅ CERTIFIED | Executable proof exists; artifact captured |
| 🔶 PARTIAL | Some proof exists; gaps documented |
| ❌ NOT STARTED | No proof exists |
| 🔴 BLOCKED | External dependency required before proceeding |

---

## Level 0 — Static / local integrity

**Status: ✅ CERTIFIED**

Single reproducible command: `bash scripts/certify.sh`

| Check | Status | Evidence |
|---|---|---|
| Shell syntax: hooks/*.sh monitors/*.sh | ✅ | certify.sh [1/10] PASS |
| Python compile: all scripts | ✅ | certify.sh [2/10] PASS |
| Phase machine: consistent | ✅ | certify.sh [3/10] PASS — validate_phase_machine.py |
| Hook regression: 15/15 | ✅ | certify.sh [4/10] PASS — test_prforge_regressions.sh |
| Preflight smoke: all pass | ✅ | certify.sh [5/10] PASS — test_preflight.sh |
| Memory indexing regression | ✅ | certify.sh [6/10] PASS — test_memory_indexing_regression.py |
| Candidate scoring regression | ✅ | certify.sh [7/10] PASS — test_candidate_scoring_regression.py |
| Mesh Redis integration: 7/7 | ✅ | certify.sh [8/10] PASS — test_mesh_redis_integration.py |
| Artifact pollution: none | ✅ | certify.sh [9/10] PASS |
| No repo-local .prforge dirs | ✅ | certify.sh [10/10] PASS |

**Evidence commit:** `f00a2b1` — PR #6 (feat/issue-1-certify-sh), merged 2026-05-07

---

## Level 1 — Single local end-to-end PR simulation

**Status: ❌ NOT STARTED**

| Artifact | Status | Expected path |
|---|---|---|
| `state.json` at each phase | ❌ | `$ARTIFACT_DIR/state.json` |
| `contract.md` | ❌ | `$ARTIFACT_DIR/contract.md` |
| `patch_plan.md` | ❌ | `$ARTIFACT_DIR/patch_plan.md` |
| `dod.md` | ❌ | `$ARTIFACT_DIR/dod.md` |
| `repo_intelligence.md` | ❌ | `$ARTIFACT_DIR/repo_intelligence.md` |
| `validation_ledger.md` | ❌ | `$ARTIFACT_DIR/validation_ledger.md` |
| `hostile_review.md` | ❌ | `$ARTIFACT_DIR/hostile_review.md` |
| `pr_body.md` | ❌ | `$ARTIFACT_DIR/pr_body.md` |
| `approval.md` | ❌ | `$ARTIFACT_DIR/approval.md` |
| `postmortem.json` | ❌ | `$ARTIFACT_DIR/postmortem.json` |
| memory record in SQLite | ❌ | `PRFORGE_MEMORY_DB` |

**Next proof action:** Run `bash scripts/tests/level1/simulate_full_run.sh` and capture output.

---

## Level 2 — Real repo dry-run verifier

**Status: 🔶 PARTIAL — verifier under development**

Issue #4 (feat/issue-4-level2-verifier) implements the dry-run verifier script that
validates all artifacts exist before allowing any public action. Once merged, Level 2
certification requires running PRForge against a real third-party repo with `--dry-run`
and confirming the verifier passes.

**Next proof action:** Merge #4, select a target repo, run with `--dry-run`.

---

## Level 3 — Approved public-action certification

**Status: 🔶 PARTIAL — two PRs merged, queue active**

PRForge dogfooded against its own repository (B-A-M-N/prforge):

| PR | Issue | What was fixed | Outcome |
|---|---|---|---|
| #5 | #3 | Reconcile legacy shipped.md with canonical v1.5 phase model | MERGED 2026-05-07 |
| #6 | #1 | Add Level 0 certification runner (certify.sh), 10/10 pass | MERGED 2026-05-07 |

**Remaining queue:** Issue #2 (this PR), Issue #4.

**Trusted-repo auto-merge policy active:** PRs from B-A-M-N authored by B-A-M-N
with all 12 criteria met are auto-squash-merged. No external repo uses auto-merge.

---

## Level 4 — Review-response certification

**Status: ❌ NOT STARTED**

**Prerequisite:** Level 3 certified with a PR that received review comments.

---

## Level 5 — CI-fix certification

**Status: ❌ NOT STARTED**

**Prerequisite:** Level 3 certified with a PR that triggered failing CI.

---

## Level 6 — Memory improvement certification

**Status: ❌ NOT STARTED**

**Infrastructure status:** Memory ledger + indexer + preflight injector unit-tested (certify.sh
check [6/10] passes). Cross-run behavioral proof (postmortem→lesson→preflight recall) absent.

**Prerequisite:** At least two Level 1/2 runs completed to produce postmortems.

---

## Level 7 — Candidate discovery usefulness

**Status: ❌ NOT STARTED**

**Infrastructure status:** Scoring engine exists, deterministic fixture passes (certify.sh
check [7/10] passes). Real-repo ranking not yet validated.

**Next proof action:** Run `/pr` in candidate_discovery mode on a live repo and review output quality.

---

## Level 8 — Distributed mesh certification

**Status: 🔶 PARTIAL**

| Check | Status | Evidence | Gap |
|---|---|---|---|
| Redis backend unit tests | ✅ | `test_mesh_redis_integration.py` 7/7 (certify.sh [8/10]) | — |
| Coordinator code compiles | ✅ | `py_compile` passes | — |
| Worker code compiles | ✅ | `py_compile` passes | — |
| Validate mesh script runs | ✅ | `validate_mesh.sh` syntax clean | — |
| Live Redis multi-worker run | ❌ | Not run | Requires Redis on LAN or localhost |
| Lease acquire/renew/release | ❌ | Not proven live | — |
| Same-file conflict prevention | ❌ | Not proven live | — |
| Stale lease recovery | ❌ | Not proven live | — |
| Dirty worktree quarantine | ❌ | Not proven live | — |

**Blocker:** Redis must be running. Worker processes must be started on LAN machine.

---

## Level 9 — Maintainer-grade outcome certification

**Status: ❌ NOT STARTED**

**Prerequisite:** Levels 1–3 completed with real PR outcomes on external repos.

**Metrics template:** See `prforge-ultimate-certification-plan.md` Level 9 metrics table.

---

## Summary

| Level | Status | Blocking on |
|---|---|---|
| 0 — Static integrity | ✅ CERTIFIED | — |
| 1 — Local simulation | ❌ NOT STARTED | `simulate_full_run.sh` creation + run |
| 2 — Real repo dry-run | 🔶 PARTIAL | Issue #4 verifier merge + target selection |
| 3 — Public actions | 🔶 PARTIAL | More PRs (queue active: #2, #4) |
| 4 — Review response | ❌ NOT STARTED | Level 3 with review comments |
| 5 — CI fix | ❌ NOT STARTED | Level 3 with failing CI |
| 6 — Memory improvement | ❌ NOT STARTED | Two completed runs |
| 7 — Candidate discovery | ❌ NOT STARTED | Live repo run |
| 8 — Mesh live | 🔶 PARTIAL | Live Redis + multi-worker |
| 9 — Maintainer-grade outcomes | ❌ NOT STARTED | Levels 1–3 on external repos |

**Verdict as of 2026-05-07:** PRForge is **Level 0 certified** and **Level 3 partially certified**
via two self-dogfood PRs. It is not yet production/contribution-engine ready — Levels 1–9 prove
the actual outcome goal. Level 0 now has a single reproducible proof command: `bash scripts/certify.sh`.
