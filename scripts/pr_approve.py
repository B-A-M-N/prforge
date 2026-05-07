#!/usr/bin/env python3
"""Executable PRForge approval/public-action verifier."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


PUBLIC_TEXT_ACTIONS = {"post_comment", "issue_comment", "review", "edit_pr", "create_pr"}


def run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return proc.stdout


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def full_diff_hash(repo: Path) -> str:
    unstaged = subprocess.run(
        ["git", "-C", str(repo), "diff", "--binary", "--full-index"],
        capture_output=True,
        timeout=10,
        check=False,
    ).stdout
    staged = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--binary", "--full-index"],
        capture_output=True,
        timeout=10,
        check=False,
    ).stdout
    return sha256_bytes(unstaged + b"\0PRFORGE-STAGED\0" + staged)


def legacy_stat_diff_hash(repo: Path) -> str:
    unstaged = run_git(repo, "diff", "--stat").encode()
    staged = run_git(repo, "diff", "--cached", "--stat").encode()
    return hashlib.sha256(unstaged).hexdigest() + hashlib.sha256(staged).hexdigest()


def resolve_artifact_dir(repo: Path, explicit: str = "") -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    pointer = repo / ".prforge-run"
    if pointer.exists() and not pointer.is_symlink():
        for line in pointer.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("artifact_dir="):
                return Path(line.split("=", 1)[1]).expanduser().resolve()
    return repo / ".prforge"


def load_state(artifact_dir: Path) -> dict:
    with (artifact_dir / "state.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


def action_from_command(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if len(parts) >= 2 and parts[0] == "git" and parts[1] == "push":
        if any(p == "--force-with-lease" for p in parts):
            return "force_push"
        if any(p == "--force" or p == "-f" or p.startswith("--force=") for p in parts):
            return "raw_force_push"
        return "push"
    if len(parts) >= 3 and parts[0] == "gh" and parts[1] == "pr":
        return {
            "create": "create_pr",
            "comment": "post_comment",
            "review": "review",
            "edit": "edit_pr",
            "merge": "merge_pr",
            "close": "close_pr",
            "ready": "ready_pr",
            "reopen": "reopen_pr",
            "request-reviewers": "request_review",
        }.get(parts[2], f"gh_pr_{parts[2]}")
    if len(parts) >= 3 and parts[0] == "gh" and parts[1] == "issue":
        return {
            "comment": "issue_comment",
            "close": "close_issue",
            "edit": "edit_issue",
        }.get(parts[2], f"gh_issue_{parts[2]}")
    if len(parts) >= 2 and parts[0] == "gh" and parts[1] == "api":
        if any(token in command for token in ("/comments", "/reviews", "/pulls", "/issues", "/merges")):
            return "gh_api_public_write"
    return ""


def command_uses_upstream_push(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return len(parts) >= 3 and parts[0:2] == ["git", "push"] and parts[2] == "upstream"


def raw_force_requested(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not (len(parts) >= 2 and parts[0:2] == ["git", "push"]):
        return False
    return any(p == "--force" or p == "-f" or p.startswith("--force=") for p in parts)


def body_text_from_command(command: str, repo: Path) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return ""
    for i, part in enumerate(parts):
        if part == "--body" and i + 1 < len(parts):
            return parts[i + 1]
        if part.startswith("--body="):
            return part.split("=", 1)[1]
        if part == "--body-file" and i + 1 < len(parts):
            raw = parts[i + 1]
            raw = raw.replace("$ARTIFACT_DIR", os.environ.get("ARTIFACT_DIR", ""))
            raw = os.path.expandvars(raw)
            path = Path(raw)
            if not path.is_absolute():
                path = repo / path
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return ""
    return ""


def approved_public_texts(state: dict, artifact_dir: Path) -> list[str]:
    texts = []
    public_text = state.get("public_text") or {}
    aliases = [
        "review_response",
        "review_comment",
        "issue_comment",
        "pr_body",
        "pr_body_update",
    ]
    for key in aliases:
        value = public_text.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value.strip())
    for name in ("review_response.md", "pr_body.md"):
        path = artifact_dir / name
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    texts.append(text)
            except OSError:
                pass
    return list(dict.fromkeys(texts))


def non_harness_dirty_files(repo: Path) -> list[str]:
    dirty = run_git(repo, "status", "--porcelain").splitlines()
    files: list[str] = []
    for line in dirty:
        path = line[3:] if len(line) > 3 else line
        if path == ".prforge-run" or path.startswith(".prforge/") or path.startswith(".prforge-"):
            continue
        files.append(path)
    return files


def tracked_artifacts(repo: Path) -> list[str]:
    files = run_git(repo, "ls-files").splitlines()
    return [
        f for f in files
        if f == ".prforge-run" or f.startswith(".prforge/") or f.startswith(".prforge-")
    ]


def verify_command(repo: Path, artifact_dir: Path, command: str) -> tuple[bool, list[str], str]:
    issues: list[str] = []
    action = action_from_command(command)
    if not action:
        return True, [], ""
    if action == "raw_force_push" or raw_force_requested(command):
        issues.append("raw git push --force is never allowed; use --force-with-lease and approve force_push")
    if command_uses_upstream_push(command):
        issues.append("push to upstream is blocked; push to origin/fork only")

    try:
        state = load_state(artifact_dir)
    except Exception as exc:
        return False, [f"state.json unreadable: {exc}"], action

    approval = state.get("approval") or {}
    release = state.get("release") or {}
    if state.get("phase") != "APPROVAL":
        issues.append(f"state.phase is {state.get('phase')!r}, not APPROVAL")
    if approval.get("consumed") is True:
        issues.append("approval has already been consumed")
    if approval.get("stale") is True:
        issues.append("approval is marked stale")
    if approval.get("approved") is not True and not approval.get("approved_at"):
        issues.append("approval.approved=true or approval.approved_at is required before public actions")
    if release.get("approval_status") == "BLOCKED":
        issues.append("release.approval_status is BLOCKED")

    approved_actions = approval.get("approved_actions") or []
    if action not in approved_actions:
        issues.append(f"action {action!r} is not in approval.approved_actions")

    required = ["approval.md", "validation_ledger.md", "dod.md"]
    for name in required:
        if not (artifact_dir / name).is_file():
            issues.append(f"required artifact missing: {name}")

    stored_diff = approval.get("diff_hash") or ""
    current_diff = full_diff_hash(repo)
    if stored_diff and stored_diff != current_diff:
        if stored_diff == legacy_stat_diff_hash(repo):
            issues.append("approval.diff_hash uses legacy diff-stat hashing; regenerate approval with full diff hash")
        else:
            issues.append("approval.diff_hash does not match current full diff")
    elif not stored_diff:
        issues.append("approval.diff_hash missing")

    file_hashes = {
        "validation_hash": artifact_dir / "validation_ledger.md",
        "approval_md_hash": artifact_dir / "approval.md",
    }
    for key, path in file_hashes.items():
        stored = approval.get(key) or ""
        if not stored:
            issues.append(f"approval.{key} missing")
        elif path.is_file() and sha256_file(path) != stored:
            issues.append(f"approval.{key} mismatch for {path.name}")

    dod_path = artifact_dir / "dod.md"
    dod_stored = state.get("dod", {}).get("generation_hash") or approval.get("dod_hash") or ""
    if not dod_stored:
        issues.append("dod generation hash missing")
    elif dod_path.is_file() and sha256_file(dod_path) != dod_stored:
        issues.append("dod.md hash mismatch; DoD artifact changed after generation")

    dirty = non_harness_dirty_files(repo)
    if dirty:
        issues.append("uncommitted non-PRForge files present: " + ", ".join(dirty[:10]))

    artifacts = tracked_artifacts(repo)
    if artifacts:
        issues.append("PRForge artifacts are tracked in git: " + ", ".join(artifacts[:10]))

    if action in PUBLIC_TEXT_ACTIONS:
        approved_texts = approved_public_texts(state, artifact_dir)
        body = body_text_from_command(command, repo).strip()
        if action in {"post_comment", "issue_comment", "review", "edit_pr"}:
            if not body:
                issues.append(f"{action} requires --body or --body-file text that was previewed")
            elif body not in approved_texts:
                issues.append(f"{action} body does not exactly match state.public_text preview")
        if action == "create_pr":
            if not body:
                issues.append("create_pr requires --body-file/--body text previewed in approval")
            elif body not in approved_texts:
                issues.append("create_pr body does not exactly match approved PR body preview")

    return not issues, issues, action


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify PRForge approval integrity")
    parser.add_argument("command", nargs="?", default="")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--artifact-dir", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    artifact_dir = resolve_artifact_dir(repo, args.artifact_dir)
    ok, issues, action = verify_command(repo, artifact_dir, args.command)
    if args.json:
        print(json.dumps({"ok": ok, "action": action, "issues": issues}, indent=2))
    elif ok:
        print("OK")
    else:
        print("FAIL:" + " | ".join(issues))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
