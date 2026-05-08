#!/usr/bin/env python3
"""Regression tests for the postmortem_generator → memory_indexer → FTS pipeline.

Covers the dict-repo schema mismatch that caused memory_indexer.py to crash
when postmortem_generator.py wrote an object-form repo (from state.json) into
postmortem.json, which SQLite could not bind to a TEXT column.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
LEDGER = SCRIPTS / "memory_ledger.py"
INDEXER = SCRIPTS / "memory_indexer.py"
GENERATOR = SCRIPTS / "postmortem_generator.py"


def run(cmd: list[str], env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=30, check=False
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(cmd)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _make_env(db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PRFORGE_MEMORY_DB"] = str(db_path)
    return env


# ── Test 1: get_postmortem_summary handles string summary without crashing ────

def test_string_summary_returns_empty():
    sys.path.insert(0, str(SCRIPTS))
    from memory_indexer import get_postmortem_summary  # noqa: PLC0415

    pm_str = json.dumps({
        "run_id": "r1",
        "repo": "org/repo",
        "summary": "a plain string instead of a dict",
    })
    result = get_postmortem_summary(pm_str)
    assert result == {}, f"expected empty dict for string summary, got {result!r}"
    print("PASS  get_postmortem_summary handles string summary → returns {}")


# ── Test 2: indexer handles dict-form repo in postmortem without crashing ─────

def test_indexer_dict_repo_in_postmortem():
    with tempfile.TemporaryDirectory(prefix="prforge-pipeline-t2.") as td:
        tmp = Path(td)
        db = tmp / "memory.db"
        run_dir = tmp / "run"
        run_dir.mkdir()
        env = _make_env(db)

        run([sys.executable, str(LEDGER), "init"], env)

        postmortem = {
            "run_id": "t2-dict-repo-run",
            "repo": {"local_path": "/tmp/some-repo", "base_branch": "main", "working_branch": "fix/branch"},
            "pr_number": 0,
            "outcome": "ABANDONED",
            "confidence": "medium",
            "summary": {
                "what_was_done": ["Fixed the bug where dict repo caused SQLite binding failure."],
                "could_be_better": [],
                "avoid_next_time": [],
                "maintainer_preferences": [],
            },
            "evidence": [],
            "tags": ["test"],
        }
        pm_path = run_dir / "postmortem.json"
        pm_path.write_text(json.dumps(postmortem, indent=2), encoding="utf-8")

        result = run(
            [sys.executable, str(INDEXER), "index",
             "--postmortem", str(pm_path), "--run-dir", str(run_dir)],
            env,
        )
        assert result.returncode == 0, f"indexer crashed on dict-form repo:\n{result.stderr}"
        print("PASS  memory_indexer.py handles dict-form repo in postmortem without crashing")


# ── Test 3: generator handles dict-form repo in state.json ───────────────────

def test_generator_dict_repo_in_state():
    with tempfile.TemporaryDirectory(prefix="prforge-pipeline-t3.") as td:
        tmp = Path(td)
        run_dir = tmp / "run"
        run_dir.mkdir()

        state = {
            "version": "1.0",
            "run_id": "t3-gen-dict-repo",
            "phase": "POSTMORTEM",
            "repo": {
                "local_path": "/tmp/target-repo",
                "base_branch": "main",
                "working_branch": "fix/validate-age-raise-value-error",
            },
            "task": {"type": "local_task", "objective": "Fix validate_age to raise ValueError"},
            "permissions": {"may_edit": True, "may_run_tests": True, "may_commit": True,
                            "may_push": False, "may_post_comments": False, "may_force_push": False},
            "started_at": "2026-05-08T00:00:00Z",
        }
        (run_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

        output_path = run_dir / "postmortem.json"
        env = os.environ.copy()
        result = run(
            [sys.executable, str(GENERATOR), "generate",
             "--run-dir", str(run_dir), "--output", str(output_path)],
            env,
        )
        assert result.returncode == 0, f"generator failed:\n{result.stderr}"
        assert output_path.exists(), "postmortem.json not written"

        pm = json.loads(output_path.read_text())
        assert isinstance(pm["repo"], str), f"repo should be a string, got {type(pm['repo'])}: {pm['repo']!r}"
        assert pm["repo"] != "", "repo should be non-empty string"
        assert isinstance(pm["summary"], dict), f"summary should be a dict, got {type(pm['summary'])}"
        assert pm["branch"] == "fix/validate-age-raise-value-error", f"wrong branch: {pm['branch']!r}"
        assert pm["run_id"] == "t3-gen-dict-repo", f"wrong run_id: {pm['run_id']!r}"
        print(f"PASS  postmortem_generator.py extracts string repo={pm['repo']!r} from dict-form state.json")


# ── Test 4: full pipeline — generator → indexer → FTS recall ─────────────────

def test_full_pipeline_generator_indexer_fts():
    with tempfile.TemporaryDirectory(prefix="prforge-pipeline-t4.") as td:
        tmp = Path(td)
        db = tmp / "memory.db"
        run_dir = tmp / "run"
        (run_dir / "git").mkdir(parents=True)
        env = _make_env(db)

        state = {
            "version": "1.0",
            "run_id": "t4-full-pipeline",
            "phase": "POSTMORTEM",
            "repo": {
                "local_path": str(tmp / "target-repo"),
                "base_branch": "main",
                "working_branch": "fix/pipeline-test",
            },
            "task": {"type": "local_task", "objective": "Test the full generator-indexer-FTS pipeline"},
            "permissions": {"may_edit": True, "may_run_tests": True, "may_commit": True,
                            "may_push": False, "may_post_comments": False, "may_force_push": False},
            "started_at": "2026-05-08T00:00:00Z",
        }
        (run_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        (run_dir / "git" / "final.diff").write_text("", encoding="utf-8")

        pm_path = run_dir / "postmortem.json"
        gen = run(
            [sys.executable, str(GENERATOR), "generate",
             "--run-dir", str(run_dir), "--output", str(pm_path)],
            env,
        )
        assert gen.returncode == 0, f"generator failed:\n{gen.stderr}"
        pm = json.loads(pm_path.read_text())
        assert isinstance(pm["repo"], str), f"generator must produce string repo, got {type(pm['repo'])}"

        run([sys.executable, str(LEDGER), "init"], env)

        idx = run(
            [sys.executable, str(INDEXER), "index",
             "--postmortem", str(pm_path), "--run-dir", str(run_dir)],
            env,
        )
        assert idx.returncode == 0, f"indexer failed:\n{idx.stderr}"

        query = run(
            [sys.executable, str(INDEXER), "query", "--query", "pipeline"],
            env,
        )
        assert query.returncode == 0, f"FTS query failed:\n{query.stderr}"
        print("PASS  full pipeline: generator → indexer → FTS query completed without error")


def main() -> int:
    tests = [
        test_string_summary_returns_empty,
        test_indexer_dict_repo_in_postmortem,
        test_generator_dict_repo_in_state,
        test_full_pipeline_generator_indexer_fts,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as exc:
            print(f"FAIL  {t.__name__}: {exc}")
            failed += 1
    if failed:
        print(f"\n{failed}/{len(tests)} tests FAILED")
        return 1
    print(f"\n{len(tests)}/{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
