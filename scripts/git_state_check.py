#!/usr/bin/env python3
"""PRForge git state and base freshness gate.

Produces $ARTIFACT_DIR/git_state.json (and optionally git_state.md) with:
  - branch, HEAD sha, base branch, upstream sha, merge-base
  - ahead/behind counts, dirty worktree, remote tracking info
  - existing PR number and mergeable/check state (requires gh)
  - recommended state:
      OK_TO_PACKAGE   — safe to run PACKAGE phase
      OK_TO_PUSH      — safe to push and open/update PR
      REBASE_REQUIRED — base has moved; rebase before any public action
      REVIEW_REFRESH  — PR exists with stale comments or pending CI
      BLOCKED         — dirty worktree, wrong branch, or conflicting state
      DEGRADED_NO_GH  — gh unavailable; partial information only

Exit codes:
  0  — OK_TO_PACKAGE or OK_TO_PUSH
  1  — REBASE_REQUIRED or REVIEW_REFRESH (warning, caller decides)
  2  — BLOCKED
  3  — usage / I/O error
  4  — DEGRADED_NO_GH (partial; no crash)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git(*args: str, cwd: Path | None = None) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=15,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def gh(*args: str, cwd: Path | None = None) -> tuple[int, str, str]:
    result = subprocess.run(
        ["gh", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=20,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _sha(ref: str, cwd: Path | None) -> str | None:
    rc, out, _ = git("rev-parse", "--verify", ref, cwd=cwd)
    return out if rc == 0 and out else None


def _count(range_: str, cwd: Path | None) -> int:
    rc, out, _ = git("rev-list", "--count", range_, cwd=cwd)
    try:
        return int(out) if rc == 0 else 0
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# State collector
# ---------------------------------------------------------------------------

PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "release", "trunk"})


def collect(repo_root: Path) -> dict[str, Any]:
    state: dict[str, Any] = {}

    # Branch
    rc, branch, _ = git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_root)
    state["branch"] = branch if rc == 0 else "DETACHED"
    state["on_protected_branch"] = state["branch"] in PROTECTED_BRANCHES

    # HEAD sha
    state["head_sha"] = _sha("HEAD", repo_root) or "unknown"

    # Tracking branch
    rc, tracking, _ = git(
        "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
        cwd=repo_root
    )
    state["tracking_branch"] = tracking if rc == 0 else None
    state["tracks_upstream"] = (
        tracking is not None and tracking.startswith("upstream/")
    )

    # Remote
    rc, origin_url, _ = git("remote", "get-url", "origin", cwd=repo_root)
    state["origin_remote"] = origin_url if rc == 0 else None

    rc, upstream_url, _ = git("remote", "get-url", "upstream", cwd=repo_root)
    state["upstream_remote"] = upstream_url if rc == 0 else None

    # Base branch — prefer origin/main, then origin/master, then origin/develop
    base_branch: str | None = None
    base_sha: str | None = None
    for candidate in ("origin/main", "origin/master", "origin/develop", "origin/trunk"):
        sha = _sha(candidate, repo_root)
        if sha:
            base_branch = candidate
            base_sha = sha
            break
    state["base_branch"] = base_branch
    state["base_sha"] = base_sha

    # Merge-base
    merge_base: str | None = None
    if base_branch:
        rc, mb, _ = git("merge-base", "HEAD", base_branch, cwd=repo_root)
        merge_base = mb if rc == 0 and mb else None
    state["merge_base"] = merge_base

    # Ahead / behind
    if base_branch:
        state["commits_ahead"] = _count(f"{base_branch}..HEAD", repo_root)
        state["commits_behind"] = _count(f"HEAD..{base_branch}", repo_root)
    else:
        state["commits_ahead"] = 0
        state["commits_behind"] = 0

    # Tracking divergence
    if tracking:
        state["tracking_ahead"] = _count(f"{tracking}..HEAD", repo_root)
        state["tracking_behind"] = _count(f"HEAD..{tracking}", repo_root)
    else:
        state["tracking_ahead"] = None
        state["tracking_behind"] = None

    # Dirty worktree
    rc, dirty_out, _ = git("status", "--porcelain", cwd=repo_root)
    state["dirty_worktree"] = bool(dirty_out) if rc == 0 else True
    state["dirty_files"] = [l.strip() for l in dirty_out.splitlines() if l.strip()] if rc == 0 else []

    # Stash
    rc, stash_out, _ = git("stash", "list", cwd=repo_root)
    state["stash_count"] = len(stash_out.splitlines()) if rc == 0 and stash_out else 0

    # Base freshness
    state["base_stale"] = state["commits_behind"] > 0

    return state


def collect_gh(repo_root: Path, branch: str) -> dict[str, Any]:
    gh_state: dict[str, Any] = {"gh_available": False}

    # Check gh auth
    rc, _, _ = gh("auth", "status", cwd=repo_root)
    if rc != 0:
        gh_state["gh_auth"] = False
        return gh_state
    gh_state["gh_auth"] = True
    gh_state["gh_available"] = True

    # Look up existing PR for current branch
    rc, out, _ = gh(
        "pr", "view", branch,
        "--json", "number,state,mergeable,statusCheckRollup,reviewDecision,comments",
        cwd=repo_root,
    )
    if rc != 0 or not out:
        gh_state["pr_number"] = None
        gh_state["pr_state"] = None
        gh_state["pr_mergeable"] = None
        gh_state["pr_ci_state"] = None
        gh_state["pr_stale_checks"] = False
        return gh_state

    try:
        pr = json.loads(out)
    except json.JSONDecodeError:
        gh_state["pr_parse_error"] = True
        return gh_state

    gh_state["pr_number"] = pr.get("number")
    gh_state["pr_state"] = pr.get("state")
    gh_state["pr_mergeable"] = pr.get("mergeable")
    gh_state["pr_review_decision"] = pr.get("reviewDecision")

    checks = pr.get("statusCheckRollup") or []
    states = {c.get("conclusion") or c.get("state") for c in checks}
    if "FAILURE" in states or "ERROR" in states:
        gh_state["pr_ci_state"] = "failed"
    elif "PENDING" in states or "IN_PROGRESS" in states:
        gh_state["pr_ci_state"] = "pending"
    elif states <= {"SUCCESS", "NEUTRAL", "SKIPPED", None}:
        gh_state["pr_ci_state"] = "passing"
    else:
        gh_state["pr_ci_state"] = "unknown"

    gh_state["pr_stale_checks"] = gh_state["pr_ci_state"] in ("failed", "pending")

    return gh_state


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------

def recommend(git_info: dict[str, Any], gh_info: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """Return (recommendation, blocking_reasons, warning_reasons)."""
    blocking: list[str] = []
    warnings: list[str] = []

    if git_info.get("dirty_worktree"):
        dirty = git_info.get("dirty_files", [])
        blocking.append(f"dirty worktree ({len(dirty)} file(s) uncommitted)")

    if git_info.get("on_protected_branch"):
        blocking.append(f"on protected branch: {git_info['branch']!r} — must use a feature branch")

    if git_info.get("tracks_upstream"):
        blocking.append(
            f"branch tracks upstream ({git_info['tracking_branch']!r}) — "
            "public push would target the upstream, not your fork"
        )

    behind = git_info.get("commits_behind", 0)
    if behind > 0:
        blocking.append(f"branch is {behind} commit(s) behind base — rebase required before public action")

    t_behind = git_info.get("tracking_behind")
    t_ahead = git_info.get("tracking_ahead")
    if t_behind and t_behind > 0 and t_ahead and t_ahead > 0:
        blocking.append(
            f"branch has diverged from tracking ({t_ahead} ahead, {t_behind} behind) — "
            "resolve before push"
        )

    if not gh_info.get("gh_available"):
        warnings.append("gh CLI unavailable or not authenticated — GitHub state unknown")
        if blocking:
            return "BLOCKED", blocking, warnings
        return "DEGRADED_NO_GH", blocking, warnings

    if gh_info.get("pr_stale_checks"):
        warnings.append(
            f"existing PR #{gh_info.get('pr_number')} has CI state: {gh_info.get('pr_ci_state')} — "
            "review before push"
        )

    if gh_info.get("pr_mergeable") == "CONFLICTING":
        blocking.append(f"PR #{gh_info.get('pr_number')} has merge conflicts")

    if blocking:
        if any("rebase" in r for r in blocking):
            return "REBASE_REQUIRED", blocking, warnings
        return "BLOCKED", blocking, warnings

    if warnings:
        stale_check_warn = any("CI state" in w for w in warnings)
        if stale_check_warn:
            return "REVIEW_REFRESH", blocking, warnings

    return "OK_TO_PUSH", blocking, warnings


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_json(artifact_dir: Path, data: dict[str, Any]) -> None:
    out = artifact_dir / "git_state.json"
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_md(artifact_dir: Path, data: dict[str, Any]) -> None:
    git_info = data.get("git", {})
    gh_info = data.get("gh", {})
    rec = data.get("recommended_state", "unknown")
    blocking = data.get("blocking_reasons", [])
    warnings = data.get("warning_reasons", [])

    lines: list[str] = [
        "# PRForge Git State\n",
        f"**Recommendation:** `{rec}`\n",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| Branch | `{git_info.get('branch', '?')}` |",
        f"| HEAD | `{git_info.get('head_sha', '?')[:12]}` |",
        f"| Base | `{git_info.get('base_branch', 'unknown')}` |",
        f"| Ahead | {git_info.get('commits_ahead', 0)} |",
        f"| Behind | {git_info.get('commits_behind', 0)} |",
        f"| Dirty | {'yes' if git_info.get('dirty_worktree') else 'no'} |",
        f"| Protected branch | {'yes' if git_info.get('on_protected_branch') else 'no'} |",
        f"| Tracks upstream | {'yes' if git_info.get('tracks_upstream') else 'no'} |",
        f"| PR number | {gh_info.get('pr_number', 'none')} |",
        f"| PR CI state | {gh_info.get('pr_ci_state', 'n/a')} |",
        "",
    ]
    if blocking:
        lines.append("## Blocking Reasons")
        lines.extend(f"- {r}" for r in blocking)
        lines.append("")
    if warnings:
        lines.append("## Warnings")
        lines.extend(f"- {w}" for w in warnings)
        lines.append("")

    out = artifact_dir / "git_state.md"
    out.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_EXIT_FOR_STATE = {
    "OK_TO_PACKAGE": 0,
    "OK_TO_PUSH": 0,
    "REBASE_REQUIRED": 1,
    "REVIEW_REFRESH": 1,
    "BLOCKED": 2,
    "DEGRADED_NO_GH": 4,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check git branch state before PRForge public actions."
    )
    parser.add_argument("artifact_dir", help="Path to the run artifact directory")
    parser.add_argument(
        "--repo", default=".", help="Path to the git repository root (default: CWD)"
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON to stdout")
    parser.add_argument("--md", action="store_true", help="Also write git_state.md")
    args = parser.parse_args(argv)

    artifact_dir = Path(args.artifact_dir)
    if not artifact_dir.is_dir():
        print(f"error: artifact_dir not found: {artifact_dir}", file=sys.stderr)
        return 3

    repo_root = Path(args.repo).resolve()

    git_info = collect(repo_root)
    gh_info = collect_gh(repo_root, git_info.get("branch", "HEAD"))

    rec, blocking, warnings = recommend(git_info, gh_info)

    data: dict[str, Any] = {
        "recommended_state": rec,
        "blocking_reasons": blocking,
        "warning_reasons": warnings,
        "git": git_info,
        "gh": gh_info,
    }

    write_json(artifact_dir, data)
    if args.md:
        write_md(artifact_dir, data)

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"git-state-gate: {rec}")
        for r in blocking:
            print(f"  BLOCKED: {r}")
        for w in warnings:
            print(f"  WARNING: {w}")

    return _EXIT_FOR_STATE.get(rec, 2)


if __name__ == "__main__":
    raise SystemExit(main())
