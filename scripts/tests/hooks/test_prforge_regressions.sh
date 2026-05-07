#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

pass() {
  echo "PASS: $1"
}

assert_contains() {
  local needle="$1"
  local file="$2"
  if ! grep -q "$needle" "$file"; then
    echo "--- $file ---" >&2
    sed -n '1,120p' "$file" >&2 || true
    fail "expected '$needle' in $file"
  fi
}

TMP="$(mktemp -d /tmp/prforge-regressions.XXXXXX)"
REPO="$TMP/repo"
mkdir -p "$REPO"
cd "$REPO"
git init -q
git config user.email audit@example.com
git config user.name Audit
printf 'one\n' > a.txt
git add a.txt
git commit -q -m init
mkdir -p .prforge
cat > .prforge/state.json <<JSON
{
  "version": "1.0",
  "phase": "INVESTIGATE",
  "repo": {
    "local_path": "$REPO",
    "base_branch": "main",
    "working_branch": "master"
  },
  "task": {
    "type": "local_task",
    "objective": "regression"
  },
  "permissions": {},
  "started_at": "2026-05-06T00:00:00Z"
}
JSON

for hook in "$ROOT"/hooks/*.sh "$ROOT"/monitors/*.sh; do
  bash -n "$hook"
done
pass "shell syntax"

if grep -R "hook_events.log" "$ROOT/hooks" >/dev/null 2>&1; then
  fail "hooks still write diagnostic hook_events.log in the repo hot path"
fi
pass "hooks avoid repo-local diagnostic event writes"

if grep -n 'find "$HOME" -path' "$ROOT/hooks/preflight.sh" "$ROOT/hooks/phase-injector.sh" >/dev/null 2>&1; then
  fail "preflight or phase injector still performs unbounded HOME scans"
fi
pass "hot-path hooks avoid unbounded HOME scans"

QUIET_REPO="$TMP/quiet-repo"
mkdir -p "$QUIET_REPO"
git -C "$QUIET_REPO" init -q
git -C "$QUIET_REPO" config user.email audit@example.com
git -C "$QUIET_REPO" config user.name Audit
printf 'quiet\n' > "$QUIET_REPO/q.txt"
git -C "$QUIET_REPO" add q.txt
git -C "$QUIET_REPO" commit -q -m init
(
  cd "$QUIET_REPO"
  printf '{"tool_name":"Read","tool_input":{"file_path":"q.txt"}}' | bash "$ROOT/hooks/gitnexus-intelligence.sh" >/dev/null 2>/dev/null
  printf '{"tool_name":"Edit","tool_input":{"file_path":"q.txt"}}' | bash "$ROOT/hooks/phase-boundary.sh" >/dev/null 2>/dev/null
  printf '{"tool_name":"Write","tool_input":{"file_path":"q.txt"}}' | bash "$ROOT/hooks/blast-radius.sh" >/dev/null 2>/dev/null
)
if [ -e "$QUIET_REPO/.prforge-run" ] || [ -e "$QUIET_REPO/.prforge" ]; then
  fail "read-only/inactive hooks created PRForge repo-local state"
fi
pass "inactive hooks do not create repo-local state"

SAFE_JSON='{"tool_name":"Bash","tool_input":{"command":"git status --short"}}'
if ! printf '%s' "$SAFE_JSON" | bash "$ROOT/hooks/phase-gate-enforcer.sh" >"$TMP/safe.out" 2>"$TMP/safe.err"; then
  fail "phase gate blocked safe git status"
fi
if [ -s "$TMP/safe.err" ]; then
  fail "phase gate emitted stderr for safe git status"
fi
pass "phase gate allows read-only Bash"

BLOCK_JSON='{"tool_name":"Bash","tool_input":{"command":"git push origin master"}}'
if printf '%s' "$BLOCK_JSON" | bash "$ROOT/hooks/phase-gate-enforcer.sh" >"$TMP/block.out" 2>"$TMP/block.err"; then
  fail "phase gate allowed git push during INVESTIGATE"
fi
assert_contains "does not permit git write" "$TMP/block.err"
pass "phase gate blocks git write"

printf 'two\n' > a.txt
EDIT_JSON="$(
  python3 - <<PY
import json
print(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "$REPO/a.txt"}}))
PY
)"
if ! printf '%s' "$EDIT_JSON" | bash "$ROOT/hooks/blast-radius.sh" >"$TMP/blast.out" 2>"$TMP/blast.err"; then
  fail "blast-radius hook failed"
fi
if [ -s "$TMP/blast.err" ]; then
  echo "--- blast stderr ---" >&2
  cat "$TMP/blast.err" >&2
  fail "blast-radius emitted stderr"
fi
python3 - <<'PY'
import json
from pathlib import Path
d = json.loads(Path(".prforge/state.json").read_text())
assert isinstance(d.get("blast_radius", {}).get("changed_files_count"), int)
PY
pass "blast radius writes numeric state quietly"

python3 "$ROOT/scripts/prforge_state.py" validate .prforge/state.json >/dev/null
printf '{"phase": "NOT_A_PHASE"}\n' > "$TMP/bad-state.json"
if python3 "$ROOT/scripts/prforge_state.py" validate "$TMP/bad-state.json" >"$TMP/bad-state.out" 2>&1; then
  fail "state validator accepted invalid state"
fi
assert_contains "required field missing" "$TMP/bad-state.out"
printf '{"phase": "SHIPPED", "blocker": "old blocker"}\n' > "$TMP/legacy-state.json"
python3 "$ROOT/scripts/prforge_state.py" migrate "$TMP/legacy-state.json" >"$TMP/legacy-state.out"
python3 "$ROOT/scripts/prforge_state.py" validate "$TMP/legacy-state.json" >/dev/null
assert_contains "MIGRATED" "$TMP/legacy-state.out"
printf '{"version": "1.0", "phase": ' > "$TMP/corrupt-state.json"
python3 "$ROOT/scripts/prforge_state.py" recover "$TMP/corrupt-state.json" >"$TMP/recover.out"
python3 "$ROOT/scripts/prforge_state.py" validate "$TMP/corrupt-state.json" >/dev/null
assert_contains "RECOVERED" "$TMP/recover.out"
pass "state helper validates schema, migrates legacy state, and recovers corrupt JSON"


DB="$TMP/memory.db"
PRFORGE_MEMORY_DB="$DB" python3 "$ROOT/scripts/memory_ledger.py" init >/dev/null
PRFORGE_MEMORY_DB="$DB" python3 - <<'PY'
import sqlite3
conn = sqlite3.connect(__import__("os").environ["PRFORGE_MEMORY_DB"])
conn.execute(
    "INSERT INTO runs (run_id, repo, started_at, run_dir) VALUES (?, ?, ?, ?)",
    ("run-1", "org/repo", "2026-05-06T00:00:00Z", "/tmp/run-1"),
)
conn.execute(
    "INSERT INTO postmortems (id, run_id, repo, outcome, summary_json, evidence_json, tags_json, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    ("pm-1", "run-1", "org/repo", "MERGED", "{}", "{}", "[]", "high", "2026-05-06T00:00:00Z"),
)
conn.commit()
PY
PRFORGE_MEMORY_DB="$DB" python3 "$ROOT/scripts/memory_ledger.py" add-memory-record \
  --postmortem-id pm-1 \
  --run-id run-1 \
  --lesson "Prefer focused regression tests for parser fixes" \
  --lesson-type test_expectations \
  --repo org/repo \
  --subsystem src \
  --file-globs-json '["src/*.py"]' \
  --evidence-artifact-ids-json '["artifact-1"]' >/dev/null

PRFORGE_MEMORY_DB="$DB" python3 "$ROOT/scripts/preflight_injector.py" \
  --repo org/repo \
  --files src/parser.py >"$TMP/inject.out"
assert_contains "Prefer focused regression tests" "$TMP/inject.out"
pass "memory preflight recalls ledger records"

mkdir -p "$TMP/validation-artifacts"
cat > "$TMP/validation-artifacts/state.json" <<JSON
{
  "version": "1.0",
  "phase": "VALIDATE",
  "memory_context": {"memory_run_id": "run-1"},
  "validation": {
    "commands_run": [{"command": "npm test", "status": "passed"}],
    "commands_not_run": []
  }
}
JSON
cat > "$TMP/validation-artifacts/validation_ledger.md" <<'EOF'
# Validation Ledger

## npm test

Status: passed
Output hash: sha256:abc123
This entry is long enough to be treated as real evidence.
EOF
PRFORGE_MEMORY_DB="$DB" python3 "$ROOT/scripts/memory_ledger.py" append-event \
  --run-id run-1 \
  --phase VALIDATE \
  --type bash_command_result \
  --payload '{"command":"npm test","exit_code":0,"stdout_sha256":"abc","stderr_sha256":"def"}' >/dev/null
PRFORGE_MEMORY_DB="$DB" python3 "$ROOT/scripts/validation_evidence.py" "$TMP/validation-artifacts" >/dev/null
python3 - <<PY
import json
from pathlib import Path
p = Path("$TMP/validation-artifacts/state.json")
d = json.loads(p.read_text())
d["validation"]["commands_run"] = [{"command": "npm run unexecuted", "status": "passed"}]
p.write_text(json.dumps(d))
PY
if PRFORGE_MEMORY_DB="$DB" python3 "$ROOT/scripts/validation_evidence.py" "$TMP/validation-artifacts" >"$TMP/validation-bypass.out" 2>&1; then
  fail "validation evidence accepted an unexecuted command"
fi
assert_contains "no command evidence" "$TMP/validation-bypass.out"
pass "validation evidence rejects unrun command claims"

git add a.txt
git commit -q -m "update fixture"
cat > .prforge/approval.md <<'EOF'
# Approval

Approved command: git push origin master
EOF
cat > .prforge/validation_ledger.md <<'EOF'
# Validation Ledger

## npm test

Status: passed
Output hash: sha256:abc123
This entry is long enough to be treated as real evidence.
EOF
cat > .prforge/dod.md <<'EOF'
# Definition of Done

- [x] Fixture update is committed
EOF
APPROVAL_HASH=$(sha256sum .prforge/approval.md | awk '{print $1}')
VAL_HASH=$(sha256sum .prforge/validation_ledger.md | awk '{print $1}')
DOD_HASH=$(sha256sum .prforge/dod.md | awk '{print $1}')
DIFF_HASH=$(python3 - <<'PY'
import hashlib, subprocess
u = subprocess.run(["git", "diff", "--binary", "--full-index"], capture_output=True).stdout
s = subprocess.run(["git", "diff", "--cached", "--binary", "--full-index"], capture_output=True).stdout
print(hashlib.sha256(u + b"\0PRFORGE-STAGED\0" + s).hexdigest())
PY
)
cat > .prforge/state.json <<JSON
{
  "version": "1.0",
  "phase": "APPROVAL",
  "release": {"approval_status": "READY_TO_SHIP"},
  "approval": {
    "approval_id": "approval-1",
    "approved": true,
    "approved_actions": ["push"],
    "diff_hash": "$DIFF_HASH",
    "validation_hash": "$VAL_HASH",
    "approval_md_hash": "$APPROVAL_HASH"
  },
  "dod": {"generation_hash": "$DOD_HASH"}
}
JSON
if python3 "$ROOT/scripts/pr_approve.py" --repo "$REPO" "git push --force origin master" >"$TMP/raw-force.out" 2>&1; then
  fail "approval verifier allowed raw force push"
fi
assert_contains "raw git push --force" "$TMP/raw-force.out"
if python3 "$ROOT/scripts/pr_approve.py" --repo "$REPO" "git push upstream master" >"$TMP/upstream.out" 2>&1; then
  fail "approval verifier allowed upstream push"
fi
assert_contains "push to upstream" "$TMP/upstream.out"
if python3 "$ROOT/scripts/pr_approve.py" --repo "$REPO" "git push -u upstream master" >"$TMP/upstream-option.out" 2>&1; then
  fail "approval verifier allowed option-form upstream push"
fi
assert_contains "push to upstream" "$TMP/upstream-option.out"
python3 "$ROOT/scripts/pr_approve.py" --repo "$REPO" "git push origin master" >/dev/null
pass "approval verifier blocks raw force/upstream and accepts approved push"

cat > .prforge/pr_body.md <<'EOF'
## Summary
- Fixture PR body

## Validation
- npm test — passed
EOF
cat > .prforge/review_response.md <<'EOF'
Commit: abc123

Addressed the requested fixture update.
EOF
PR_BODY_HASH=$(sha256sum .prforge/pr_body.md | awk '{print $1}')
APPROVAL_HASH=$(sha256sum .prforge/approval.md | awk '{print $1}')
DIFF_HASH="$DIFF_HASH" VAL_HASH="$VAL_HASH" APPROVAL_HASH="$APPROVAL_HASH" DOD_HASH="$DOD_HASH" python3 - <<'PY'
import json
import os
from pathlib import Path
p = Path(".prforge/state.json")
d = {
    "version": "1.0",
    "phase": "APPROVAL",
    "release": {"approval_status": "READY_TO_SHIP"},
    "public_text": {
        "pr_body": Path(".prforge/pr_body.md").read_text(),
        "review_response": Path(".prforge/review_response.md").read_text(),
    },
    "approval": {
        "approval_id": "approval-2",
        "approved": True,
        "approved_actions": ["create_pr", "post_comment", "review"],
        "diff_hash": os.environ["DIFF_HASH"],
        "validation_hash": os.environ["VAL_HASH"],
        "approval_md_hash": os.environ["APPROVAL_HASH"],
    },
    "dod": {"generation_hash": os.environ["DOD_HASH"]},
}
p.write_text(json.dumps(d, indent=2))
PY
ARTIFACT_DIR="$REPO/.prforge" python3 "$ROOT/scripts/pr_approve.py" --repo "$REPO" \
  'gh pr create --title "Fixture" --body-file $ARTIFACT_DIR/pr_body.md' >/dev/null
ARTIFACT_DIR="$REPO/.prforge" python3 "$ROOT/scripts/pr_approve.py" --repo "$REPO" \
  'gh pr comment 123 --body-file $ARTIFACT_DIR/review_response.md' >/dev/null
if python3 "$ROOT/scripts/pr_approve.py" --repo "$REPO" \
  'gh pr comment 123 --body "unpreviewed text"' >"$TMP/public-text.out" 2>&1; then
  fail "approval verifier accepted unpreviewed public text"
fi
assert_contains "does not exactly match" "$TMP/public-text.out"
pass "approval verifier enforces public PR body/comment previews"

PID_DIR="$TMP/monitor-pids"
PRFORGE_MONITOR_PID_DIR="$PID_DIR" PRFORGE_LOCAL_WATCH_INTERVAL=10 bash "$ROOT/monitors/local-watch.sh" >"$TMP/monitor1.out" 2>"$TMP/monitor1.err" &
MON_PID=$!
sleep 0.3
if ! kill -0 "$MON_PID" 2>/dev/null; then
  fail "local monitor exited before duplicate prevention check"
fi
PRFORGE_MONITOR_PID_DIR="$PID_DIR" PRFORGE_LOCAL_WATCH_INTERVAL=10 bash "$ROOT/monitors/local-watch.sh" >"$TMP/monitor2.out" 2>"$TMP/monitor2.err"
if [ "$(cat "$PID_DIR/prforge-local-watch.pid" 2>/dev/null)" != "$MON_PID" ]; then
  fail "duplicate monitor replaced active pid"
fi
kill "$MON_PID" 2>/dev/null || true
wait "$MON_PID" 2>/dev/null || true
if [ -f "$PID_DIR/prforge-local-watch.pid" ]; then
  fail "local monitor did not clean up pid file"
fi
PRFORGE_MONITOR_PID_DIR="$PID_DIR" PRFORGE_MONITOR_ONCE=1 bash "$ROOT/monitors/distributed-worker-watch.sh" >/dev/null
PRFORGE_MONITOR_PID_DIR="$PID_DIR" PRFORGE_MONITOR_ONCE=1 bash "$ROOT/monitors/distributed-coordinator-watch.sh" >/dev/null
pass "monitors prevent duplicates and support one-shot lifecycle"

python3 - <<PY
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, "$ROOT/scripts/mesh")
from mesh_lock_guard import normalized_config, load_json
from mesh_lock_guard import repo_relative_path, artifact_dir_for_worktree
from mesh_lock_guard import path_is_covered_by_lease
flat = {"mode": "local", "worker_id": "w1", "redis": {"url": "redis://x"}, "cluster": "c"}
nested = {"mesh": {"node_id": "w2", "redis_url": "redis://y", "cluster_name": "d"}}
config_path = Path("$TMP/mesh-config.json")
config_path.write_text(json.dumps(flat))
assert load_json(config_path)["worker_id"] == "w1"
os.environ["PRFORGE_MESH_MODE"] = "lan"
assert normalized_config(flat)["worker_id"] == "w1"
assert normalized_config(nested)["worker_id"] == "w2"
assert normalized_config(nested)["mode"] == "lan"
assert normalized_config(nested)["redis_url"] == "redis://y"
wt = Path("$REPO")
(wt / ".prforge-run").write_text("artifact_dir=$TMP/artifacts\n")
assert artifact_dir_for_worktree(wt) == Path("$TMP/artifacts")
assert repo_relative_path(str(wt / "src" / "x.py"), wt) == "src/x.py"
leases = [{"path": "src/locked.py"}, {"path": "pkg"}]
assert path_is_covered_by_lease("src/locked.py", leases)
assert path_is_covered_by_lease("pkg/module.py", leases)
assert not path_is_covered_by_lease("src/locked.py.bak", leases)
assert not path_is_covered_by_lease("src/unlocked.py", leases)
PY
pass "mesh lock guard accepts config variants and repo pointer paths"

cat > "$TMP/mesh-off-config.json" <<'JSON'
{"mode": "off", "worker_id": "w1"}
JSON
MESH_JSON="$(
  python3 - <<PY
import json
print(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "$REPO/a.txt"}}))
PY
)"
if ! printf '%s' "$MESH_JSON" | \
  HOME="$TMP/no-home-scan" \
  PRFORGE_MESH_CONFIG="$TMP/mesh-off-config.json" \
  PRFORGE_WORKER_ID="w1" \
  CLAUDE_PLUGIN_ROOT="" \
  bash "$ROOT/hooks/mesh-lock-guard.sh" >"$TMP/mesh-hook.out" 2>"$TMP/mesh-hook.err"; then
  cat "$TMP/mesh-hook.err" >&2 || true
  fail "mesh lock guard failed without CLAUDE_PLUGIN_ROOT"
fi
pass "mesh hook resolves guard without plugin root or home scan"
