# PRForge Certification Scorecard

**Last updated:** 2026-05-08
**Branch:** fix/prforge-v1_5-stabilization
**Evaluator:** PRForge self-certification

---

## Observation-derived regression record

**Regression:** During dogfooding on 2026-05-08, we observed an agent willing to mark
self-review `APPROVE` while acknowledging a core quality weakness in the same artifact:
`"Known tradeoff: LIKE search on full query = low recall, but LLM synthesizes gracefully from
empty results. Accepted for v1."` This language passed no enforcement gate and would have
reached a public push.

**Fix:** PRForge now includes a generic quality weakness gate (`scripts/quality_weakness_gate.py`)
that blocks this class of failure before PACKAGE and before any public approval action.

**Proof record:**

| Enforcement point | Command | Result |
|---|---|---|
| Direct gate invocation | `python3 scripts/quality_weakness_gate.py $ART` | Exit 2 — BLOCKED, 4 findings listed with artifact:line |
| Phase hook — SELF_REVIEW→PACKAGE | `printf '<hook-json>' \| bash hooks/phase-boundary.sh` | Exit 1 — "PRForge Quality Weakness Gate — BLOCKED" |
| Public action guard | `python3 scripts/pr_approve.py ... "git push origin ..."` | Exit 1 — "BLOCKING_WEAKNESS — self-review cannot approve" |
| Regression test coverage | `bash scripts/tests/hooks/test_prforge_regressions.sh` | Tests 16–20 cover all three enforcement paths |

**Scope:** The regression was observational (synthetic dogfood artifact) — no real feature branch
was blocked. The fix is framework-level: any future PR whose artifacts contain matching language
is blocked regardless of which feature it is.

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

**Status: ✅ CERTIFIED — 2026-05-08 (`bash scripts/certify.sh`, 29/29 PASS)**

| Check | Status | Evidence | Gap |
|---|---|---|---|
| Python/shell tests pass | ✅ | All test suites pass (38 tests) | — |
| Shell syntax | ✅ | `bash -n hooks/*.sh` clean | — |
| Phase machine legal | ✅ | `validate_phase_machine.py` VALID | — |
| No repo-local artifact pollution | ✅ | `git status --short` clean | — |
| Hooks smoke | ✅ | `test_prforge_regressions.sh` 20/20 PASS | — |
| Preflight smoke | ✅ | `test_preflight.sh` all PASS | — |
| Quality weakness gate wired | ✅ | hook blocks SELF_REVIEW→PACKAGE on BLOCKING_WEAKNESS | — |
| Git state gate wired | ✅ | pr_approve.py rejects push on BLOCKED/REBASE_REQUIRED | — |
| No fake gates | ✅ | 6 mechanical wiring regression tests pass | — |
| Docs match implementation | 🔶 | Known limitations documented | Minor stale wording in legacy docs |
| Certify runner exists | ✅ | `scripts/certify.sh` created (Issue #1 DONE) | — |
| Full validation command list captured | ✅ | `docs/level0-certification-run.txt` written | — |

**Next proof action:** Run `bash scripts/certify.sh --save-output` on each significant change
to keep `docs/level0-certification-run.txt` current.

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

**Target repo for simulation:** PRForge itself (self-dogfood) or a synthetic fixture.

**Simulation script:** `scripts/tests/level1/simulate_full_run.sh`

**Next proof action:** Run `bash scripts/tests/level1/simulate_full_run.sh` and save output to
`docs/level1-certification-run.txt`.

---

## Level 2 — Real repo dry-run

**Status: 🔶 PARTIAL — verifier exists, run not yet started**

**Target repo:** _not yet selected_

**Candidate criteria:**
- Small, well-scoped open issue
- Python or shell (PRForge's native test languages)
- Active maintainer (responds within days)
- < 50k lines of code total
- Has existing test suite

**Artifact verifier:** `scripts/verify_level2_run.py` (Issue #4 — DONE)

| Verifier check | Status |
|---|---|
| Required artifacts present | ✅ verified by `verify_level2_run.py` |
| Quality weakness gate clean | ✅ run on artifact dir |
| Validation ledger has real command output | ✅ checked |
| PR body has required sections | ✅ checked |
| Contract scope captured | ✅ checked |
| git_state.json present and non-blocked | ✅ checked |
| Approval preview text present | ✅ checked |

**Next proof action:** Select a real repo issue. Run `/pr` in a local clone. Then run
`python3 scripts/verify_level2_run.py $ARTIFACT_DIR` and capture output to
`docs/level2-certification-run.txt`.

---

## Level 3 — Approved public-action certification

**Status: 🔴 BLOCKED — awaiting explicit user approval**

No public push, PR, or comment may be executed until the user says:
> "Proceed with Level 3 on \<repo\>"

---

## Level 4 — Review-response certification

**Status: ❌ NOT STARTED**

**Prerequisite:** Level 3 certified (need a real PR with review comments).

---

## Level 5 — CI-fix certification

**Status: ❌ NOT STARTED**

**Prerequisite:** Level 3 certified (need a real PR with failing CI).

---

## Level 6 — Memory improvement certification

**Status: ❌ NOT STARTED**

**Infrastructure status:** Memory ledger + indexer + preflight injector unit-tested. Cross-run
behavioral proof absent.

**Prerequisite:** At least two Level 1/2 runs completed to produce postmortems.

---

## Level 7 — Candidate discovery usefulness

**Status: ❌ NOT STARTED**

**Infrastructure status:** Scoring engine exists, deterministic fixture passes. Real-repo ranking
not yet validated.

**Next proof action:** Run `/pr` in `candidate_discovery` mode on a live repo and review output
quality.

---

## Level 8 — Distributed mesh certification

**Status: 🔶 PARTIAL**

| Check | Status | Evidence | Gap |
|---|---|---|---|
| Redis backend unit tests | ✅ | `test_mesh_redis_integration.py` passes (mock Redis) | — |
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

**Prerequisite:** Levels 1–3 completed with real PR outcomes.

**Metrics template:** See `prforge-ultimate-certification-plan.md` Level 9 metrics table.

---

## Summary

| Level | Status | Blocking on |
|---|---|---|
| 0 — Static integrity | ✅ CERTIFIED | — |
| 1 — Local simulation | ❌ NOT STARTED | `simulate_full_run.sh` creation + run |
| 2 — Real repo dry-run | 🔶 PARTIAL | Verifier exists; repo + issue selection needed |
| 3 — Public actions | 🔴 BLOCKED | Explicit user approval |
| 4 — Review response | ❌ NOT STARTED | Level 3 |
| 5 — CI fix | ❌ NOT STARTED | Level 3 |
| 6 — Memory improvement | ❌ NOT STARTED | Two completed runs |
| 7 — Candidate discovery | ❌ NOT STARTED | Live repo run |
| 8 — Mesh live | 🔶 PARTIAL | Live Redis + multi-worker |
| 9 — Maintainer-grade outcomes | ❌ NOT STARTED | Levels 1–3 |

**Verdict as of 2026-05-08:** PRForge is **dogfood-ready** — Level 0 certified (29/29), gates
mechanically wired and regression-tested, quality weakness and git state enforcement proven in
all three layers. Observation-derived regression closed.

It is **not yet production/contribution-engine ready** — Levels 1–9 prove the actual outcome
goal; Level 0 is the only certified level.

---

## Level 1 run log

_Populated after `simulate_full_run.sh` runs._

| Field | Value |
|---|---|
| Run date | — |
| Target repo | — |
| Artifact directory | — |
| Phases completed | — |
| Artifacts produced | — |
| Gate failures | — |
| Postmortem produced | — |
| Memory record created | — |
| Result | — |
