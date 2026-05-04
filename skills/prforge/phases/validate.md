# Phase 5: VALIDATE

Read this file at the START of VALIDATE before doing any work.

---

Run the validation plan. Record honest results. **Tests are not optional — they
are a required gate.**

## Step 0: Verify Test Existence (HARD GATE)

Before running any tests, verify that every changed non-test file has
corresponding test coverage.

```bash
# Get changed non-test files
CHANGED_SRC=$(git diff --name-only 2>/dev/null | grep -vE '\.(test|spec)\.' | grep -v 'node_modules' | grep -v '.prforge')

# For each source file, check for a corresponding test
MISSING_TESTS=""
for f in $CHANGED_SRC; do
  base=$(basename "$f" | sed 's/\.[^.]*$//')
  dir=$(dirname "$f")
  found=$(find "$dir" -maxdepth 3 \( -name "${base}.test.*" -o -name "${base}.spec.*" -o -name "test_${base}.*" -o -name "${base}_test.*" \) 2>/dev/null | head -1)
  if [ -z "$found" ]; then
    MISSING_TESTS="$MISSING_TESTS\n  - $f (no test found)"
  fi
done
```

**If `MISSING_TESTS` is non-empty:**

Default: **add the tests yourself** — follow existing test patterns in the repo.
The agent may create test files without asking the user. This is expected behavior.

Only escalate to BLOCKED (and surface to user) if:
- The test framework requires a live environment or external service unavailable locally
- The changed file is infrastructure-only (CI config, build scripts, `.gitignore`, docs)
- Adding tests would require changes outside the contract scope

In those cases only, document the justification in `validation_ledger.md`:
```
### Tests Not Required
- `path/to/file.ts` — [reason: config-only / type-only / infrastructure / docs]
```

**Do NOT proceed to PACKAGE without either tests or documented justification.**

## Command Discovery

Auto-detect the repo type and available commands:

**Node/TypeScript:**
```bash
# From package.json scripts:
npm test -- <target>
npm run typecheck
npm run lint
npm run build
```

**Python:**
```bash
pytest <target>
ruff check .
mypy .
```

**Go:**
```bash
go test ./...
go test ./path/...
go vet ./...
```

**Rust:**
```bash
cargo test
cargo clippy
cargo fmt --check
```

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

- [ ] `validation_ledger.md` written with honest pass/fail/not-run for every command
- [ ] No changed source file has zero test coverage (or documented justification exists)
- [ ] `state.json` phase updated to `VALIDATE`

**You may not commit, push, or run any git write operation from this point.
SELF_REVIEW is the mandatory next step.**
