# PRForge Artifact Location

PRForge run artifacts live outside the target repository.

Resolve the active artifact directory before reading or writing run files:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
ARTIFACT_DIR=""
if [ -n "$REPO_ROOT" ] && [ -f "$REPO_ROOT/.prforge-run" ] && [ ! -L "$REPO_ROOT/.prforge-run" ]; then
  ARTIFACT_DIR=$(awk -F= '$1=="artifact_dir"{print $2}' "$REPO_ROOT/.prforge-run" | tail -1)
fi
if [ -z "$ARTIFACT_DIR" ]; then
  echo "No active PRForge artifact directory. Start or resume /pr first." >&2
  exit 1
fi
```

Rules:

- Read and write run files under `$ARTIFACT_DIR`.
- The target repo may contain only `.prforge-run`, a plain pointer file.
- `.prforge-run`, `.prforge/`, and `.prforge-*` must be excluded via `.git/info/exclude`.
- Repo-local `.prforge/` is legacy only. Do not create it for new runs.
- Do not add PRForge patterns to `.gitignore`.

Common files include `$ARTIFACT_DIR/state.json`, `task.json`,
`repo_intelligence.md`, `contract.md`, `patch_plan.md`, `dod.md`,
`validation_ledger.md`, `hostile_review.md`, `pr_body.md`,
`review_response.md`, `approval.md`, and `postmortem.json`.
