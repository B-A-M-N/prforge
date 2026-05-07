# Policy: Artifact Exclusion

Read this file at activation. Always active — applies to every mode.

---

## Rule 10: Never commit PRForge artifacts

PRForge run artifacts are internal state — `state.json`, `task.json`, `approval.md`,
`validation_ledger.md`, etc. They must NEVER be staged, tracked, or committed.

Primary storage is outside the repo:

```text
~/.prforge/runs/<repo-slug>/<branch-or-pr>/<run-id>/
```

`PRFORGE_HOME` may override the outside artifact root; default is `$HOME/.prforge`.

The repo may contain only a tiny ignored pointer file when needed:

```ini
.prforge-run
run_id=run-abc123
artifact_dir=/home/bamn/.prforge/runs/org__repo/pr-456/run-abc123
mesh_job_id=job_org_repo_456_review
```

Do not symlink `repo/.prforge` to the outside artifact directory. Symlinked
repo-local state is forbidden because tools may follow or stage it.

Committing `state.json` or any other `.prforge/` file is a hard error. It exposes
internal harness state to the public repo and signals the AI nature of the workflow.

## Git Exclusion Setup (MUST run first on every activation)

**This is the FIRST git operation on any activation.** Run before touching any other file:

```bash
# Only use .git/info/exclude — NEVER modify .gitignore for PRForge artifacts.
# .gitignore modifications are tracked changes that get pushed upstream.
# .git/info/exclude is local-only and never committed.
EXCLUDE_FILE="$REPO_ROOT/.git/info/exclude"
for pat in ".prforge/" ".prforge-run" ".prforge-*"; do
  if ! grep -qxF "$pat" "$EXCLUDE_FILE" 2>/dev/null; then
    echo "$pat" >> "$EXCLUDE_FILE"
  fi
done
```

`.git/info/exclude` is a local-only exclusion — it does not modify the repo's tracked files.

**NEVER modify `.gitignore`** to add PRForge patterns. That creates a tracked change
that would be pushed upstream, advertising the use of an AI harness to the repo.

## Guard #4: Artifact Exclusion Verification (run at SELF_REVIEW)

After all edits are complete and before packaging, verify:

```bash
# Check for staged PRForge files
git diff --cached --name-only | grep -E '(^|/)\.prforge(/|$)|(^|/)\.prforge-run$|(^|/)\.prforge-[^/]+'

# Check if any PRForge files are tracked
git ls-files | grep -E '(^|/)\.prforge(/|$)|(^|/)\.prforge-run$|(^|/)\.prforge-[^/]+'
```

**If any PRForge files are staged or tracked:**
1. Remove from staging immediately: `git reset HEAD -- .prforge/ .prforge-run .prforge-*`
2. If tracked in index: `git rm --cached -r .prforge .prforge-run .prforge-*`
3. Re-verify `.git/info/exclude` has `.prforge/`, `.prforge-run`, and `.prforge-*`
4. Redirect to `ARTIFACT_REPAIR` until `git ls-files` returns no PRForge paths

Record result in `state.json`:
```json
{
  "artifact_exclusion": {
    "clean": true
  }
}
```

`artifact_exclusion.clean == false` is a hard blocker for approval. The approval
status MUST be `BLOCKED` until this is resolved.

## If PRForge artifacts appear in `git status` as tracked or staged

This is a recoverable redirect. Public actions stay blocked, but local reads,
approved file edits, validation, and artifact repair remain allowed:

1. `git reset HEAD -- .prforge/ .prforge-run .prforge-*` — unstage all PRForge files
2. `git rm --cached -r .prforge .prforge-run .prforge-*` — remove from index if tracked
3. Verify `.git/info/exclude` contains `.prforge/`, `.prforge-run`, and `.prforge-*`
4. Verify `git ls-files | grep -E '(^|/)\.prforge(/|$)|(^|/)\.prforge-run$|(^|/)\.prforge-[^/]+'` returns empty
5. Then continue

Nothing screams "AI harness accident" like committing `.prforge/state.json`.
