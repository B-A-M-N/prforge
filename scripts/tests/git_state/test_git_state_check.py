#!/usr/bin/env python3
"""Tests for the PRForge git state and base freshness gate.

Uses synthetic git repositories (no real GitHub). gh-dependent tests
degrade gracefully when gh is unavailable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / "scripts" / "git_state_check.py"


# ---------------------------------------------------------------------------
# Git repo factory
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env,
        text=True, capture_output=True, timeout=15,
        check=check,
    )


def make_repo(td: Path) -> Path:
    """Create a bare origin + working clone. Returns the clone path."""
    origin = td / "origin.git"
    origin.mkdir()
    _git(["init", "--bare", str(origin)], cwd=td)

    clone = td / "clone"
    _git(["clone", str(origin), str(clone)], cwd=td)

    # initial commit on main
    (clone / "README.md").write_text("hello\n")
    _git(["add", "README.md"], cwd=clone)
    _git(["commit", "-m", "initial"], cwd=clone)
    _git(["push", "-u", "origin", "HEAD:main"], cwd=clone)

    return clone


def run_gate(repo: Path, artifact_dir: Path, extra_args: list[str] | None = None) -> tuple[int, dict]:
    args = [
        sys.executable, str(GATE),
        str(artifact_dir),
        "--repo", str(repo),
        "--json",
    ] + (extra_args or [])
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=20)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        data = {"raw_stdout": result.stdout, "raw_stderr": result.stderr}
    return result.returncode, data


# ---------------------------------------------------------------------------
# Test 1: Clean feature branch → OK_TO_PUSH or OK_TO_PACKAGE
# ---------------------------------------------------------------------------

def test_clean_feature_branch_ok() -> None:
    with tempfile.TemporaryDirectory(prefix="prforge-gs-t1.") as td:
        tdp = Path(td)
        repo = make_repo(tdp)
        artifact = tdp / "artifacts"
        artifact.mkdir()

        # Create a feature branch with one commit ahead
        _git(["checkout", "-b", "feature/add-cache"], cwd=repo)
        (repo / "cache.py").write_text("# cache\n")
        _git(["add", "cache.py"], cwd=repo)
        _git(["commit", "-m", "add cache"], cwd=repo)

        rc, data = run_gate(repo, artifact)
        assert rc in (0, 4), f"expected 0 or 4 (DEGRADED_NO_GH), got {rc}. state={data.get('recommended_state')}"
        state = data.get("recommended_state", "")
        assert state in ("OK_TO_PUSH", "OK_TO_PACKAGE", "DEGRADED_NO_GH"), f"unexpected state: {state}"

        git_state = data.get("git", {})
        assert git_state.get("branch") == "feature/add-cache"
        assert git_state.get("commits_ahead", 0) >= 1
        assert git_state.get("commits_behind", 0) == 0
        assert not git_state.get("dirty_worktree")
        assert not git_state.get("on_protected_branch")

        # git_state.json must be written
        assert (artifact / "git_state.json").exists()
        print("  test_clean_feature_branch_ok: PASS")


# ---------------------------------------------------------------------------
# Test 2: Dirty worktree → BLOCKED
# ---------------------------------------------------------------------------

def test_dirty_worktree_blocked() -> None:
    with tempfile.TemporaryDirectory(prefix="prforge-gs-t2.") as td:
        tdp = Path(td)
        repo = make_repo(tdp)
        artifact = tdp / "artifacts"
        artifact.mkdir()

        _git(["checkout", "-b", "feature/dirty"], cwd=repo)
        # Modify a file without committing
        (repo / "README.md").write_text("modified but not committed\n")

        rc, data = run_gate(repo, artifact)
        assert rc == 2, f"expected BLOCKED (exit 2), got {rc}"
        state = data.get("recommended_state", "")
        assert state == "BLOCKED", f"unexpected state: {state}"
        blocking = data.get("blocking_reasons", [])
        assert any("dirty" in r.lower() for r in blocking), f"expected dirty worktree in blocking: {blocking}"
        print("  test_dirty_worktree_blocked: PASS")


# ---------------------------------------------------------------------------
# Test 3: On main branch → BLOCKED
# ---------------------------------------------------------------------------

def test_on_main_branch_blocked() -> None:
    with tempfile.TemporaryDirectory(prefix="prforge-gs-t3.") as td:
        tdp = Path(td)
        repo = make_repo(tdp)
        artifact = tdp / "artifacts"
        artifact.mkdir()

        # Stay on main (default after clone)
        _git(["checkout", "main"], cwd=repo, check=False)

        rc, data = run_gate(repo, artifact)
        assert rc == 2, f"expected BLOCKED (exit 2), got {rc}"
        state = data.get("recommended_state", "")
        assert state == "BLOCKED", f"unexpected state: {state}"
        blocking = data.get("blocking_reasons", [])
        assert any("protected" in r.lower() for r in blocking), f"expected protected branch: {blocking}"
        print("  test_on_main_branch_blocked: PASS")


# ---------------------------------------------------------------------------
# Test 4: Branch tracks upstream remote → BLOCKED
# ---------------------------------------------------------------------------

def test_tracks_upstream_blocked() -> None:
    with tempfile.TemporaryDirectory(prefix="prforge-gs-t4.") as td:
        tdp = Path(td)
        repo = make_repo(tdp)
        artifact = tdp / "artifacts"
        artifact.mkdir()

        # Simulate upstream remote
        upstream = tdp / "upstream.git"
        upstream.mkdir()
        _git(["init", "--bare", str(upstream)], cwd=tdp)
        _git(["remote", "add", "upstream", str(upstream)], cwd=repo)

        _git(["checkout", "-b", "feature/upstream-track"], cwd=repo)
        (repo / "x.py").write_text("x\n")
        _git(["add", "x.py"], cwd=repo)
        _git(["commit", "-m", "x"], cwd=repo)
        # Push to upstream (simulates tracking upstream instead of origin)
        _git(["push", "upstream", "HEAD:main"], cwd=repo)
        _git(["branch", "--set-upstream-to=upstream/main"], cwd=repo)

        rc, data = run_gate(repo, artifact)
        git_info = data.get("git", {})
        # Branch tracks upstream → should detect it
        assert git_info.get("tracks_upstream"), "expected tracks_upstream=True"
        assert rc == 2, f"expected BLOCKED, got {rc}"
        blocking = data.get("blocking_reasons", [])
        assert any("upstream" in r.lower() for r in blocking), f"expected upstream in blocking: {blocking}"
        print("  test_tracks_upstream_blocked: PASS")


# ---------------------------------------------------------------------------
# Test 5: Branch behind base → REBASE_REQUIRED
# ---------------------------------------------------------------------------

def test_branch_behind_base_rebase_required() -> None:
    with tempfile.TemporaryDirectory(prefix="prforge-gs-t5.") as td:
        tdp = Path(td)
        repo = make_repo(tdp)
        artifact = tdp / "artifacts"
        artifact.mkdir()

        # Create feature branch from current base
        _git(["checkout", "-b", "feature/stale"], cwd=repo)
        (repo / "feat.py").write_text("feat\n")
        _git(["add", "feat.py"], cwd=repo)
        _git(["commit", "-m", "feat"], cwd=repo)

        # Add a commit to main (origin) after feature branched
        _git(["checkout", "main"], cwd=repo)
        (repo / "hotfix.py").write_text("hotfix\n")
        _git(["add", "hotfix.py"], cwd=repo)
        _git(["commit", "-m", "hotfix"], cwd=repo)
        _git(["push", "origin", "main"], cwd=repo)

        # Switch back to feature branch
        _git(["checkout", "feature/stale"], cwd=repo)
        # Fetch so origin/main is updated locally
        _git(["fetch", "origin"], cwd=repo)

        rc, data = run_gate(repo, artifact)
        git_info = data.get("git", {})
        assert git_info.get("commits_behind", 0) >= 1, f"expected behind > 0: {git_info}"
        # Should be REBASE_REQUIRED or BLOCKED
        assert rc in (1, 2), f"expected exit 1 or 2, got {rc}"
        state = data.get("recommended_state", "")
        assert state in ("REBASE_REQUIRED", "BLOCKED"), f"unexpected state: {state}"
        print("  test_branch_behind_base_rebase_required: PASS")


# ---------------------------------------------------------------------------
# Test 6: Diverged tracking branch → BLOCKED
# ---------------------------------------------------------------------------

def test_diverged_tracking_branch_blocked() -> None:
    with tempfile.TemporaryDirectory(prefix="prforge-gs-t6.") as td:
        tdp = Path(td)
        repo = make_repo(tdp)
        artifact = tdp / "artifacts"
        artifact.mkdir()

        # Create and push a feature branch
        _git(["checkout", "-b", "feature/diverge"], cwd=repo)
        (repo / "a.py").write_text("a\n")
        _git(["add", "a.py"], cwd=repo)
        _git(["commit", "-m", "a"], cwd=repo)
        _git(["push", "-u", "origin", "feature/diverge"], cwd=repo)

        # Add a commit locally (not pushed) AND a commit to origin (simulated via another clone)
        clone2 = tdp / "clone2"
        _git(["clone", str(tdp / "origin.git"), str(clone2)], cwd=tdp)
        _git(["checkout", "-b", "feature/diverge"], cwd=clone2)
        # Fetch the branch from origin first
        _git(["fetch", "origin", "feature/diverge"], cwd=clone2)
        _git(["checkout", "-b", "feature/diverge", "origin/feature/diverge"], cwd=clone2, check=False)
        (clone2 / "b.py").write_text("b\n")
        _git(["add", "b.py"], cwd=clone2)
        _git(["commit", "-m", "b from clone2"], cwd=clone2)
        _git(["push", "origin", "feature/diverge"], cwd=clone2)

        # Back in main clone: add a different commit → now diverged
        (repo / "c.py").write_text("c\n")
        _git(["add", "c.py"], cwd=repo)
        _git(["commit", "-m", "c local only"], cwd=repo)
        _git(["fetch", "origin"], cwd=repo)

        rc, data = run_gate(repo, artifact)
        git_info = data.get("git", {})
        t_ahead = git_info.get("tracking_ahead", 0) or 0
        t_behind = git_info.get("tracking_behind", 0) or 0
        assert t_ahead >= 1 and t_behind >= 1, f"expected diverged: ahead={t_ahead} behind={t_behind}"
        assert rc == 2, f"expected BLOCKED, got {rc}"
        print("  test_diverged_tracking_branch_blocked: PASS")


# ---------------------------------------------------------------------------
# Test 7: No gh auth → DEGRADED_NO_GH (no crash)
# ---------------------------------------------------------------------------

def test_no_gh_auth_degrades_gracefully() -> None:
    with tempfile.TemporaryDirectory(prefix="prforge-gs-t7.") as td:
        tdp = Path(td)
        repo = make_repo(tdp)
        artifact = tdp / "artifacts"
        artifact.mkdir()

        _git(["checkout", "-b", "feature/no-gh"], cwd=repo)
        (repo / "z.py").write_text("z\n")
        _git(["add", "z.py"], cwd=repo)
        _git(["commit", "-m", "z"], cwd=repo)

        # The gate should not crash regardless of gh availability
        rc, data = run_gate(repo, artifact)
        # Must not be a hard error (exit 3)
        assert rc != 3, f"gate crashed (exit 3): {data}"
        # If gh is unavailable, state should be DEGRADED_NO_GH or OK_TO_PUSH
        state = data.get("recommended_state", "")
        assert state != "", f"recommended_state must be set"
        gh_info = data.get("gh", {})
        # Must have gh info section without crashing
        assert isinstance(gh_info, dict), "gh section must be a dict"
        print("  test_no_gh_auth_degrades_gracefully: PASS")


# ---------------------------------------------------------------------------
# Test 8: git_state.json always written
# ---------------------------------------------------------------------------

def test_git_state_json_written() -> None:
    with tempfile.TemporaryDirectory(prefix="prforge-gs-t8.") as td:
        tdp = Path(td)
        repo = make_repo(tdp)
        artifact = tdp / "artifacts"
        artifact.mkdir()

        _git(["checkout", "-b", "feature/output-check"], cwd=repo)

        run_gate(repo, artifact)

        state_file = artifact / "git_state.json"
        assert state_file.exists(), "git_state.json must be written"
        state = json.loads(state_file.read_text())
        assert "recommended_state" in state
        assert "git" in state
        assert "gh" in state
        assert "blocking_reasons" in state
        assert "warning_reasons" in state
        assert state["git"]["branch"] == "feature/output-check"
        print("  test_git_state_json_written: PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    tests = [
        test_clean_feature_branch_ok,
        test_dirty_worktree_blocked,
        test_on_main_branch_blocked,
        test_tracks_upstream_blocked,
        test_branch_behind_base_rebase_required,
        test_diverged_tracking_branch_blocked,
        test_no_gh_auth_degrades_gracefully,
        test_git_state_json_written,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL {t.__name__}: {exc}")
            failed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {t.__name__}: {type(exc).__name__}: {exc}")
            failed += 1

    print(f"\ngit-state-gate tests: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
