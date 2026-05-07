# PRForge Ultimate Certification Plan

**Goal:** Prove PRForge can *repeatedly* produce smaller, safer, better-evidenced, maintainer-grade
upstream PRs with less babysitting than a raw Claude Code session.

Tests passing ≠ production readiness. Each level below targets a distinct dimension of that goal.
No level is considered certified by documentation alone — only by executable proof and observable
artifacts.

---

## Safety constraints (all levels)

- Do NOT push.
- Do NOT create public PRs/comments unless Level 3 is explicitly approved.
- Do NOT drop/pop/apply stash.
- Do NOT rebase, reset --hard, or git clean.
- Do NOT rewrite history.

---

## Level definitions

### LEVEL 0 — Static / local integrity

**Goal:** The code, hooks, and docs are internally consistent with no fake gates or pollution.

**Proof actions:**

| Check | Command | Expected |
|---|---|---|
| All Python/shell tests pass | `python -m pytest scripts/tests/ -v` | all pass |
| Shell syntax | `bash -n hooks/*.sh monitors/*.sh` | silent |
| Phase machine legal | `python3 scripts/validate_phase_machine.py` | VALID |
| No repo-local artifact pollution | `git status --short` | clean |
| Artifacts outside repo | `find . -maxdepth 3 -name '.prforge' -type d` | none |
| Hooks smoke | `bash scripts/tests/hooks/test_prforge_regressions.sh` | all PASS |
| Preflight smoke | `bash scripts/tests/hooks/test_preflight.sh` | all PASS |
| Docs reference real scripts | manual audit of references/ and docs/ | consistent |
| No legacy SHIPPED state references | `grep -r "SHIPPED" hooks/ commands/ skills/ --include='*.md' --include='*.sh'` | only in legacy/migration context |

**Current status:** CERTIFIED — `bash scripts/certify.sh` delivers 10/10 on master as of PR #6
(commit f00a2b1, merged 2026-05-07). Single reproducible proof command replaces manual checklist.

**Runner:** `scripts/certify.sh` — exits 0 on full pass, 1 on any failure, writes timestamped
report to `.prforge-certification/level0/latest.txt` (gitignored).

---

### LEVEL 1 — Single local end-to-end PR simulation

**Goal:** Prove the full PRForge pipeline (INTAKE → COMPLETE) runs end-to-end for a local task
and produces a complete, schema-valid artifact trail in outside-repo storage.

**Proof actions:**

1. Create a temp target repository with a known, small, testable defect.
2. Run `scripts/tests/level1/simulate_full_run.sh` which exercises each phase's scripts in sequence.
3. Verify every required artifact is produced and validates against schema:
   - `state.json` at each phase transition
   - `contract.md` (all required sections present)
   - `patch_plan.md` (files listed, rationale present)
   - `dod.md` (acceptance criteria present)
   - `repo_intelligence.md` (non-empty)
   - `validation_ledger.md` (at least one command entry)
   - `hostile_review.md` (checklist complete)
   - `pr_body.md` (required sections present)
   - `approval.md` (hashes present, action listed)
   - `postmortem.json` (validates against postmortem-schema.json)
   - memory record created in SQLite
4. Confirm approval preview matches intended public text.
5. Confirm no public action fires.
6. Confirm memory_ledger contains the lesson after MEMORY_INDEX.

**Current status:** NOT STARTED — no full pipeline simulation script exists.

**Next proof action:** Run `bash scripts/tests/level1/simulate_full_run.sh` and capture output to
`docs/level1-certification-run.txt`.

---

### LEVEL 2 — Real repo dry-run

**Goal:** PRForge finds relevant files in a real external repo, limits scope, writes tests or
justifies their absence, avoids unrelated changes, and generates a maintainer-grade PR body —
all without any public action.

**Proof actions:**

1. Pick a real public repo with a well-defined small issue (documented in scorecard).
2. Run `/pr <issue-url>` in a local clone of that repo.
3. After PACKAGE, verify:
   - Files touched are only within scope of the issue.
   - Test evidence is present or explicitly justified as absent.
   - PR body passes hostile review checklist.
   - No unrelated files modified.
   - Approval preview exists and is human-readable.
4. Stop before APPROVAL (no public action).
5. Collect artifact trail.

**Current status:** NOT STARTED.

**Next proof action:** Choose a real repo issue. Document it in the scorecard. Run the dry-run.

---

### LEVEL 3 — Approved public-action certification

**Goal:** Prove that when the user approves a public action, PRForge executes exactly what was
approved — no more, no less — and records the event.

**This level requires explicit user approval before starting.**

**Proof actions:**

1. Use a private or personal fork repo to limit blast radius.
2. Complete a Level 2 dry-run first.
3. Get explicit user approval: "proceed with Level 3 on <repo>".
4. Execute push + PR creation.
5. Verify:
   - Branch pushed matches approval.md branch.
   - PR body exactly matches approval.md text (no drift).
   - Commit hash included in PR body.
   - Postmortem captures the public action event.
   - Memory indexes the lesson.

**Current status:** PARTIAL — PR #5 (shipped.md, 2026-05-07) and PR #6 (certify.sh, 2026-05-07)
merged against B-A-M-N/prforge. Trusted-repo auto-merge policy active. Queue continues with #2, #4.

---

### LEVEL 4 — Review-response certification

**Goal:** PRForge fetches all reviewer comments, classifies each concern, addresses
required/blocker items, identifies optional items, refreshes review state before packaging, and
generates a non-defensive response.

**Proof actions:**

1. Use a real PR with at least one substantive reviewer comment.
2. Run `/pr <pr-url>` in `review_response` mode.
3. Verify:
   - All reviewer comments fetched and listed in artifact.
   - Each concern classified: blocker / required / optional / user-decision.
   - Blocker items addressed before PACKAGE.
   - Response text drafted without defensiveness.
   - Review state refreshed (no stale data).
   - Approval gate before posting response.

**Current status:** NOT STARTED.

---

### LEVEL 5 — CI-fix certification

**Goal:** PRForge classifies a CI failure correctly, reproduces or documents inability to
reproduce, patches only the related issue, and records validation evidence honestly.

**Proof actions:**

1. Use a real repo with a failing check.
2. Run `/pr` in `ci_fix` mode.
3. Verify:
   - CI failure classified: related / unrelated to PR changes.
   - Reproduction attempted or inability documented.
   - Only related failure patched.
   - Validation ledger records exact commands run.
   - PR body does not overclaim ("CI fixed" only if reproducibly fixed).

**Current status:** NOT STARTED.

---

### LEVEL 6 — Memory improvement certification

**Goal:** Prove that PRForge memory improves agent behavior across multiple runs — not just that
lessons are stored, but that they are recalled, used, and irrelevant lessons are ignored.

**Proof actions (across at least two runs):**

1. Run 1: complete PR, produce postmortem with a named lesson.
2. Run 2 on related repo/subsystem: verify INTAKE injects the prior lesson.
3. Verify: agent behavior on Run 2 differs from Run 1 in the direction of the lesson.
4. Introduce an unrelated repo: verify the lesson is NOT injected (no hallucinated relevance).
5. Verify stale/promoted/demoted lessons are handled correctly.

**Current status:** NOT STARTED — memory infrastructure exists and unit-tested, but cross-run
behavioral proof is absent.

---

### LEVEL 7 — Candidate discovery usefulness

**Goal:** PRForge fetches real candidates, ranks them with human-readable reasons, rejects bad
candidates, and the selected candidate leads to a successful PR attempt.

**Proof actions:**

1. Run `/pr` in `candidate_discovery` mode on a real populated repo.
2. Verify:
   - Candidates ranked with reasons (scope, risk, testability, maintainer alignment).
   - Bad candidates (stale, huge, unrelated) rejected with reason.
   - Top candidate is small, testable, and maintainer-aligned.
   - User can understand why top candidate is top from the output alone.
3. Proceed to Level 1/2 simulation on the selected candidate.

**Current status:** NOT STARTED — scoring exists and unit-tested; real-repo usefulness unproven.

---

### LEVEL 8 — Distributed mesh certification

**Goal:** Prove the mesh runs safely with real Redis and multiple workers: leases, isolation,
conflict prevention, crash recovery.

**Proof actions:**

1. Start Redis locally or on LAN (10.9.66.198).
2. Start coordinator and two workers.
3. Verify:
   - Workers register and heartbeat.
   - Leases acquired, renewed, and released.
   - Same-file conflicts prevented (two workers blocked from editing same file).
   - Stale lease detected and reclaimed after worker crash.
   - Isolated worktrees created per job.
   - Dirty worktrees quarantined.
   - No two agents edit same file unsafely.
4. Run `scripts/mesh/validate_mesh.sh` against live instance.

**Current status:** PARTIAL — mesh code exists, mock Redis tests pass, systemd installer shipped;
live Redis + multi-worker run not certified.

**Blocker:** Redis must be reachable and workers started on LAN machine.

---

### LEVEL 9 — Maintainer-grade outcome certification

**Goal:** Across multiple real PRs, measure whether PRForge consistently produces better
outcomes than an unassisted Claude Code session.

**Metrics to collect per run:**

| Metric | Target |
|---|---|
| Files touched | ≤ scope defined in contract |
| PR size | < 400 lines changed |
| Test evidence | present or explicitly justified |
| Maintainer requests changes | tracked and addressed |
| Review iterations to merge | documented |
| Scope creep | none detected |
| PRForge prevented a mistake | documented in postmortem |
| Memory improved next run | documented in lesson |
| Babysitting events | < 3 per run |

**Current status:** NOT STARTED — requires completing Levels 1–3 first.

---

## Certification runner

See `scripts/tests/certify.sh` for the automated runner that executes Levels 0–1 locally
and reports status.

---

## Revision history

| Date | Author | Change |
|---|---|---|
| 2026-05-07 | PRForge | Initial plan created |
