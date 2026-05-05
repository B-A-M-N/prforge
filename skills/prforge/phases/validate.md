# Phase 5: VALIDATE

Read this file at the START of VALIDATE before doing any work.

---

Run the validation plan. Record honest results. **Tests are not optional — they
are a required gate.**

## Step -1: Rebase Onto Latest Main (HARD GATE)

Before running any tests, rebase the PR branch onto the latest main/master.
This catches merge-base drift — where main has advanced and introduced lint
issues or behavioral changes that don't manifest on the old base.

```bash
git fetch origin
git rebase origin/main 2>/dev/null || git rebase origin/master
```

If rebase conflicts exist:
1. Resolve them
2. If conflicts are in unrelated files that you didn't touch: abort rebase, proceed with caution, document in validation_ledger.md
3. If conflicts are in your changed files: resolve and document

**You may not proceed to test running without first attempting this rebase.**
Document the rebase result in validation_ledger.md.

## Step 0: Run CI-Equivalent Commands Locally

Run the SAME commands that CI will run. Do not assume tests pass because they
passed locally on an old base. CI uses the merge commit against latest main,
which may surface:
- Lint rules that are stricter in the target Node/version environment
- Behavioral changes from upstream (e.g. fake timer + AbortSignal incompatibilities)
- Type errors from updated dependencies in main

**Discover and run CI-equivalent commands:**
```bash
# Read CI config to find exact commands
cat .github/workflows/ci.yml 2>/dev/null | grep -E 'run:|npm run' | head -20
cat package.json | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f'{k}: {v}') for k,v in d.get('scripts',{}).items() if any(t in k for t in ['test','lint','check','ci','build','type'])]"

# Run the full CI-equivalent lint with zero tolerance
npm run lint -- --max-warnings 0 2>/dev/null || npm run lint 2>/dev/null || true
npm run typecheck 2>/dev/null || true
```

**If CI uses a different Node version:** Document this in validation_ledger.md under "Not Run" with the note that CI may surface version-specific issues.

## Step 0: Verify Test Existence (HARD GATE)

Before running any tests, verify that every changed non-test file has
corresponding test coverage. Auto-detect test files by scanning for common
patterns in the repo — do NOT assume a specific language or test framework.

```bash
# Get changed non-test files (exclude common non-source patterns)
CHANGED_SRC=$(git diff --name-only 2>/dev/null | grep -vE '\.(test|spec)\.' | grep -v 'node_modules' | grep -v '.prforge' | grep -vE '\.(md|json|yaml|yml|toml|cfg|conf|lock|map|d\.ts)$')

# Discover test file patterns used in this repo
# Look for any file that references the changed source file
MISSING_TESTS=""
for f in $CHANGED_SRC; do
  base=$(basename "$f")
  name="${base%.*}"
  dir=$(dirname "$f")
  # Search for test files that import/reference this source file
  found=$(grep -rl "$base\|$name" "$dir" 2>/dev/null | grep -iE '(test|spec|_test|test_)' | head -1)
  if [ -z "$found" ]; then
    # Also check common test directories
    found=$(find . -maxdepth 4 -path "*/test*" -name "*${name}*" 2>/dev/null | head -1)
  fi
  if [ -z "$found" ]; then
    MISSING_TESTS="$MISSING_TESTS\n  - $f (no test found)"
  fi
done
```

**If `MISSING_TESTS` is non-empty:**

Default: **add the tests yourself** — follow existing test patterns in the repo.
Discover the test framework by looking at existing test files, then match their patterns.

Only escalate to BLOCKED (and surface to user) if:
- The test framework requires a live environment or external service unavailable locally
- The changed file is infrastructure-only (CI config, build scripts, `.gitignore`, docs)
- Adding tests would require changes outside the contract scope

In those cases only, document the justification in `validation_ledger.md`:
```
### Tests Not Required
- `path/to/file.ext` — [reason: config-only / type-only / infrastructure / docs]
```

**Do NOT proceed to PACKAGE without either tests or documented justification.**

## Command Discovery (Language-Agnostic)

Auto-detect available test and validation commands by inspecting the repo's
build configuration. Do NOT assume a specific language.

```bash
# 1. Check package.json scripts (Node/TS)
cat package.json 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
scripts=d.get('scripts',{})
for k,v in scripts.items():
    if any(t in k for t in ['test','lint','check','ci','build','type']):
        print(f'  {k}: {v}')
"

# 2. Check Makefile
grep -E '^[a-zA-Z_-]+:' Makefile 2>/dev/null | head -10

# 3. Check task runners (Taskfile, Justfile, etc.)
cat Taskfile.yml 2>/dev/null | grep -E '^[a-zA-Z_-]+:' | head -10
cat justfile 2>/dev/null | grep -E '^[a-zA-Z_-]+:' | head -10

# 4. Check CI config for exact commands
cat .github/workflows/ci.yml 2>/dev/null | grep -E 'run:|npm|pip|cargo|go |python|make ' | head -20
cat .circleci/config.yml 2>/dev/null | grep -E 'run:|command:' | head -10

# 5. Check for common config files
ls tox.ini pytest.ini setup.py pyproject.toml Cargo.toml go.mod build.gradle pom.xml CMakeLists.txt 2>/dev/null
```

Run ALL discovered test/lint/typecheck commands. If a command fails, document
whether it's related to your changes or pre-existing.

## Validation Order

1. **Targeted tests** — run tests specifically for changed files
2. **Package-level tests** — the broader test suite for the affected package
3. **Typecheck/lint** — if available
4. **Full suite** — only if reasonable (not for large monorepos)

## Output: `validation_ledger.md`

```markdown
# Validation Ledger

## Test Coverage
- Changed source files: N
- Files with test coverage: N
- Files without tests (justified): N
- Missing test files: [list or "None"]

## Passed
- `npm test -- packages/core/test/config/foo.test.ts` — 12 tests passed
- `npm run typecheck` — passed

## Failed
- `npm run lint` — failed
  - Cause: pre-existing lint issue in unrelated file
  - Related to changes: No
  - Evidence: [brief excerpt]

## Not Run
- Full CI — unavailable locally

## Tests Not Required
- `path/to/config.ts` — config-only file, no logic to test
```

**Non-negotiable:** Never claim tests passed unless actually run. Never write
"should pass" or "appears to work." Either "Ran and passed:" or "Not run:" with
a reason. Every changed source file must have tests or a documented justification.

---

## PHASE EXIT GATE — VALIDATE

Before advancing to SELF_REVIEW, all of the following must be true:

- [ ] Branch rebased onto latest main/master (or rebase attempted and documented)
- [ ] CI-equivalent commands discovered and run locally (not just project tests)
- [ ] `validation_ledger.md` written with honest pass/fail/not-run for every command
- [ ] No changed source file has zero test coverage (or documented justification exists)
- [ ] Lint passes with zero warnings (or failures documented as pre-existing/unrelated)
- [ ] `state.json` phase updated to `VALIDATE`

**You may not commit, push, or run any git write operation from this point.
SELF_REVIEW is the mandatory next step.**
