#!/usr/bin/env python3
"""PRForge Terminal Snapshot — captures evidence before POSTMORTEM transition.

Called by phase-boundary.sh during APPROVAL phase, before allowing transition
to POSTMORTEM. Captures all terminal artifacts and registers them in the
memory ledger. Artifact files must always exist — "none found" is recorded
with a metadata status, not an empty file.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid

MEMORY_LEDGER = None  # set after args parsed

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def run_ledger(cmd_args):
    """Run memory_ledger.py as subprocess."""
    import importlib.util
    ledger_path = os.path.join(os.path.dirname(__file__), "memory_ledger.py")
    result = subprocess.run(
        [sys.executable, ledger_path] + cmd_args,
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        print(f"ledger error: {result.stderr}", file=sys.stderr)
    return result.returncode == 0

def capture_pr_json(run_dir, repo=None):
    """Capture github/pr.json via gh pr view."""
    path = os.path.join(run_dir, "github", "pr.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "number,title,url,state,headRefName,baseRefName,mergeCommit,commits"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            with open(path, 'w') as f:
                f.write(result.stdout)
            return path
    except Exception:
        pass
    # Write empty placeholder if gh fails
    with open(path, 'w') as f:
        json.dump({"status": "pr_not_found"}, f)
    return path

def capture_review_comments(run_dir, repo=None):
    """Capture github/review-comments.jsonl via gh api."""
    path = os.path.join(run_dir, "github", "review-comments.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        result = subprocess.run(
            ["gh", "pr", "view", "--json", "comments"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            comments = data.get("comments", [])
            if comments:
                with open(path, 'w') as f:
                    for c in comments:
                        f.write(json.dumps(c) + "\n")
                return path
    except Exception:
        pass
    # Write empty marker
    with open(path, 'w') as f:
        json.dump({"status": "no_review_comments_found", "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f)
    return path

def capture_ci_runs(run_dir, repo=None):
    """Capture github/ci-runs.jsonl via gh run list."""
    path = os.path.join(run_dir, "github", "ci-runs.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        result = subprocess.run(
            ["gh", "run", "list", "--json", "name,status,conclusion,url,startedAt"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if data:
                with open(path, 'w') as f:
                    for run in data:
                        f.write(json.dumps(run) + "\n")
                return path
    except Exception:
        pass
    # Write empty marker
    with open(path, 'w') as f:
        json.dump({"status": "no_ci_runs_found", "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f)
    return path

def capture_final_diff(run_dir):
    """Capture git/final.diff — the diff for the entire PR branch."""
    path = os.path.join(run_dir, "git", "final.diff")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "diff", "main...HEAD"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            with open(path, 'w') as f:
                f.write(result.stdout)
            return path
    except Exception:
        pass
    with open(path, 'w') as f:
        f.write("")
    return path

def capture_commits_jsonl(run_dir):
    """Capture git/commits.jsonl — all commits in the PR."""
    path = os.path.join(run_dir, "git", "commits.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "log", "main..HEAD", "--pretty=format:%H %s"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            with open(path, 'w') as f:
                for line in result.stdout.split("\n"):
                    if line.strip():
                        parts = line.split(" ", 1)
                        sha = parts[0]
                        subject = parts[1] if len(parts) > 1 else ""
                        f.write(json.dumps({"sha": sha, "subject": subject}) + "\n")
            return path
    except Exception:
        pass
    return path

def main():
    parser = argparse.ArgumentParser(description="PRForge Terminal Snapshot")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--state", default="")
    args = parser.parse_args()

    run_dir = args.run_dir
    run_id = os.path.basename(run_dir)

    # Read state to get outcome
    state = {}
    if args.state and os.path.isfile(args.state):
        with open(args.state) as f:
            state = json.load(f)

    outcome = state.get("outcome")
    if not outcome:
        print("ERROR: outcome not set in state.json", file=sys.stderr)
        sys.exit(1)

    print(f"Capturing terminal snapshot for run {run_id} (outcome={outcome})")

    artifacts = [
        ("github/pr.json", capture_pr_json(run_dir)),
        ("github/review-comments.jsonl", capture_review_comments(run_dir)),
        ("github/ci-runs.jsonl", capture_ci_runs(run_dir)),
        ("git/final.diff", capture_final_diff(run_dir)),
        ("git/commits.jsonl", capture_commits_jsonl(run_dir)),
    ]

    all_ok = True
    for rel_path, abs_path in artifacts:
        if not abs_path or not os.path.isfile(abs_path):
            print(f"ERROR: artifact not captured: {rel_path}", file=sys.stderr)
            all_ok = False
            continue

        sha = sha256_file(abs_path)
        ok = run_ledger([
            "add-artifact",
            "--run-id", run_id,
            "--type", rel_path.split("/")[0],  # github or git
            "--path", abs_path,
            "--run-dir", run_dir,
        ])
        if not ok:
            print(f"ERROR: failed to register artifact: {rel_path}", file=sys.stderr)
            all_ok = False
        else:
            print(f"  Registered: {rel_path} ({sha[:12]}...)")

    if not all_ok:
        print("ERROR: terminal snapshot incomplete", file=sys.stderr)
        sys.exit(1)

    # Log terminal_snapshot event
    run_ledger([
        "append-event",
        "--run-id", run_id,
        "--phase", "APPROVAL",
        "--type", "terminal_snapshot_complete",
        "--payload", json.dumps({"outcome": outcome, "artifacts": len(artifacts)}),
    ])

    print(f"Terminal snapshot complete: {len(artifacts)} artifacts registered")
    sys.exit(0)

if __name__ == "__main__":
    main()
