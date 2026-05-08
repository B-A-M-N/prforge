#!/usr/bin/env python3
"""Postmortem Generator — analyzes PR lifecycle artifacts to produce postmortem.json.

Usage:
    python postmortem_generator.py generate --run-dir . --output postmortem.json

Reads artifacts from the run directory and generates a structured postmortem
capturing what happened, what could be better, and what to avoid next time.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse sha256_text from memory_ledger.py
sys.path.insert(0, str(Path(__file__).parent))
from memory_ledger import sha256_text


def _read_json(path, default=None):
    """Read a JSON file. Return default if missing or invalid."""
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _read_jsonl(path):
    """Read a JSONL file, yielding one parsed object per line."""
    if not os.path.isfile(path):
        return []
    results = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass
    return results


def _read_text(path, default=""):
    """Read a text file."""
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


def _safe_get(obj, *keys, default=None):
    """Safely traverse nested dicts/lists."""
    for k in keys:
        if isinstance(obj, dict):
            obj = obj.get(k)
        elif isinstance(obj, list) and isinstance(k, int) and 0 <= k < len(obj):
            obj = obj[k]
        else:
            return default
        if obj is None:
            return default
    return obj


def collect_evidence(run_dir):
    """Collect all evidence from run artifacts."""
    evidence = []
    run_dir = Path(run_dir)

    # --- review comments ---
    review_comments = _read_jsonl(run_dir / "github" / "review-comments.jsonl")
    for rc in review_comments:
        comment_id = _safe_get(rc, "id", default="unknown")
        permalink = _safe_get(rc, "permalink", default="")
        body = _safe_get(rc, "body", default="")
        path = _safe_get(rc, "path", default="")
        # Clean up body for quote: first non-empty line or first 200 chars
        quote_lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
        quote = quote_lines[0][:200] if quote_lines else body[:200]
        if body and path:
            evidence.append({
                "type": "review_comment",
                "id": f"comment-{comment_id}",
                "url": permalink if permalink else f"#comment-{comment_id}",
                "quote": quote,
                "file": path,
            })

    # --- ci runs ---
    ci_runs = _read_jsonl(run_dir / "github" / "ci-runs.jsonl")
    for run in ci_runs:
        name = _safe_get(run, "name", default="ci")
        conclusion = _safe_get(run, "conclusion", default="unknown")
        url = _safe_get(run, "url", default="")
        # Include all runs so we have full picture; failures are especially important
        evidence.append({
            "type": "ci_run",
            "name": name,
            "conclusion": conclusion,
            "url": url,
        })

    # --- commits ---
    commits = _read_jsonl(run_dir / "git" / "commits.jsonl")
    for commit in commits:
        sha = _safe_get(commit, "sha", default="")
        files = _safe_get(commit, "files", default=[])
        if sha:
            # files may be a list of paths or list of dicts with 'filename'
            file_list = []
            for f in files:
                if isinstance(f, dict):
                    fn = _safe_get(f, "filename") or _safe_get(f, "file")
                    if fn:
                        file_list.append(fn)
                elif isinstance(f, str):
                    file_list.append(f)
            evidence.append({
                "type": "commit",
                "sha": sha[:8],
                "files": file_list,
            })

    # --- diff stats ---
    final_diff = run_dir / "git" / "final.diff"
    if final_diff.is_file():
        try:
            content = final_diff.read_text(encoding="utf-8")
            files_changed = 0
            additions = 0
            deletions = 0
            for line in content.split("\n"):
                if line.startswith("diff --git"):
                    files_changed += 1
                elif line.startswith("+") and not line.startswith("+++"):
                    additions += 1
                elif line.startswith("-") and not line.startswith("---"):
                    deletions += 1
            evidence.append({
                "type": "diff_stats",
                "files_changed": files_changed,
                "additions": additions,
                "deletions": deletions,
            })
        except OSError:
            pass

    return evidence


def build_summary(state, evidence, contract_text, validation_text, decomposition_text):
    """Build a rich summary from actual artifact content."""
    what_was_done = []
    could_be_better = []
    avoid_next_time = []
    maintainer_preferences = []

    # Determine PR purpose from state
    phase = _safe_get(state, "phase", default="")
    outcome = _safe_get(state, "outcome", default="")
    task_desc = _safe_get(state, "task", default={}).get("description", "") if isinstance(_safe_get(state, "task"), dict) else _safe_get(state, "task", default="")
    if isinstance(task_desc, str) and task_desc:
        what_was_done.append(f"Completed task: {task_desc}")
    elif phase:
        what_was_done.append(f"Phase: {phase}")

    # CI evidence tells us about test/integration health
    ci_failures = [e for e in evidence if e.get("type") == "ci_run" and e.get("conclusion") in ("failure", "error", "cancelled")]
    ci_successes = [e for e in evidence if e.get("type") == "ci_run" and e.get("conclusion") in ("success", "skipped")]

    if ci_failures:
        failed_names = [e.get("name", "unknown") for e in ci_failures]
        could_be_better.append(f"CI runs failed: {', '.join(failed_names)}. Investigate flaky tests or environment issues.")
        if outcome == "MERGED":
            could_be_better.append("PR was merged despite CI failures — ensure failures are acceptable or well-documented.")
    elif ci_successes:
        what_was_done.append("All CI checks passed.")

    # Review comments reveal maintainer preferences and concerns
    review_comments = [e for e in evidence if e.get("type") == "review_comment"]
    for rc in review_comments:
        quote = rc.get("quote", "")
        rc_file = rc.get("file", "")
        lower_q = quote.lower()
        # Common maintainer feedback patterns
        if any(word in lower_q for word in ["prefer", "should", "please", "consider", "recommend"]):
            maintainer_preferences.append({
                "preference": quote.strip(),
                "inferred": True,
                "evidence": f"Review comment on {rc_file}",
            })
        if any(word in lower_q for word in ["fix", "change", "bug", "issue", "error"]):
            avoid_next_time.append(f"Addressed concern in {rc_file}: {quote[:100]}")
        if any(word in lower_q for word in ["test", "coverage", "spec", "unit"]):
            could_be_better.append(f"Maintainer requested more testing for {rc_file} — add tests in this area.")

    if not maintainer_preferences and review_comments:
        # Generic preference extraction from review body
        for rc in review_comments[:3]:
            body = rc.get("quote", "")
            if len(body) > 20:
                maintainer_preferences.append({
                    "preference": body[:150] + ("..." if len(body) > 150 else ""),
                    "inferred": True,
                    "evidence": f"Review comment on {rc.get('file', 'unknown')}",
                })

    # Contract, validation, decomposition files capture process lessons
    for label, text in [("contract", contract_text), ("validation", validation_text), ("decomposition", decomposition_text)]:
        if text and len(text.strip()) > 20:
            lines = [ln.strip() for ln in text.split("\n") if ln.strip() and not ln.strip().startswith("#")]
            if lines:
                what_was_done.append(f"Documented {label}: {lines[0][:100]}")

    # Diff stats — scope of change
    diff_stats = [e for e in evidence if e.get("type") == "diff_stats"]
    if diff_stats:
        ds = diff_stats[0]
        files = ds.get("files_changed", 0)
        adds = ds.get("additions", 0)
        dels = ds.get("deletions", 0)
        what_was_done.append(f"Modified {files} files (+{adds}/-{dels} lines).")
        if files > 10:
            could_be_better.append("Large change set — consider splitting into smaller, focused PRs next time.")

    # Commits
    commit_evidence = [e for e in evidence if e.get("type") == "commit"]
    if len(commit_evidence) > 3:
        could_be_better.append(f"Many commits ({len(commit_evidence)}) — squash or group logically for clarity.")

    # Ensure each category has at least one item
    if not what_was_done:
        what_was_done.append(f"Completed PR lifecycle (phase: {phase or 'unknown'}, outcome: {outcome or 'unknown'}).")
    if not could_be_better:
        could_be_better.append("Process ran smoothly — keep following this approach.")
    if not avoid_next_time:
        avoid_next_time.append("Continue to write clear commit messages and comprehensive tests.")

    return {
        "what_was_done": what_was_done,
        "could_be_better": could_be_better,
        "avoid_next_time": avoid_next_time,
        "maintainer_preferences": maintainer_preferences,
    }


def generate_postmortem(run_dir, output_path):
    """Generate postmortem.json from run artifacts."""
    run_dir = Path(run_dir).resolve()
    output_path = Path(output_path).resolve()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- State ---
    state_path = run_dir / "state.json"
    state = _read_json(state_path, {})

    run_id = _safe_get(state, "run_id", default=None) or os.path.basename(str(run_dir))
    repo_raw = _safe_get(state, "repo", default=None)
    pr_data = _safe_get(state, "pr") or {}
    if isinstance(repo_raw, dict):
        # PRForge state.json stores repo as an object — extract a string identifier
        repo = (repo_raw.get("github")
                or os.path.basename((repo_raw.get("local_path") or "").rstrip("/"))
                or "unknown-repo")
        branch = (repo_raw.get("working_branch")
                  or _safe_get(state, "branch", default=pr_data.get("branch", "unknown")))
    else:
        repo = repo_raw or "unknown-repo"
        branch = _safe_get(state, "branch", default=pr_data.get("branch", "unknown"))
    pr_number = _safe_get(state, "pr_number") or pr_data.get("number") or 0
    if isinstance(pr_number, str):
        try:
            pr_number = int(pr_number)
        except ValueError:
            pr_number = 0
    outcome = _safe_get(state, "outcome", default="UNKNOWN").upper()
    phase = _safe_get(state, "phase", default="")
    merged_at = _safe_get(state, "merged_at", default=now)
    if not merged_at or merged_at == "null":
        # Infer from completed_at or now
        merged_at = _safe_get(state, "completed_at", default=now) or now

    # --- Evidence ---
    evidence = collect_evidence(run_dir)

    # --- Additional sources ---
    contract_text = _read_text(run_dir / "contract.md")
    validation_text = _read_text(run_dir / "validation_ledger.md")
    decomposition_text = _read_text(run_dir / "review_decomposition.md")

    # --- Summary ---
    summary = build_summary(state, evidence, contract_text, validation_text, decomposition_text)

    # --- Tags ---
    tags = []
    if outcome == "MERGED":
        tags.append("merged")
    elif outcome == "CLOSED":
        tags.append("closed")
    ci_failures = [e for e in evidence if e.get("type") == "ci_run" and e.get("conclusion") == "failure"]
    if ci_failures:
        tags.append("ci-failures")
    if len([e for e in evidence if e.get("type") == "commit"]) > 3:
        tags.append("many-commits")
    diff_stats = [e for e in evidence if e.get("type") == "diff_stats"]
    if diff_stats and diff_stats[0].get("files_changed", 0) > 10:
        tags.append("large-change")
    tags.append(f"phase-{phase}" if phase else "phase-unknown")

    # --- Confidence ---
    confidence = "high" if evidence and len(evidence) >= 3 else "medium" if evidence else "low"

    postmortem = {
        "run_id": run_id,
        "repo": repo,
        "pr_number": pr_number,
        "branch": branch,
        "outcome": outcome,
        "merged_at": merged_at,
        "summary": summary,
        "evidence": evidence,
        "tags": tags,
        "confidence": confidence,
    }

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(postmortem, f, indent=2, ensure_ascii=False)

    print(f"Postmortem written to {output_path}")
    return postmortem


def main():
    parser = argparse.ArgumentParser(description="Generate a postmortem.json from PR lifecycle artifacts.")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # generate command
    generate_parser = subparsers.add_parser("generate", help="Generate postmortem from artifacts")
    generate_parser.add_argument("--run-dir", default=".", help="Run directory containing artifacts (default: .)")
    generate_parser.add_argument("--output", default="postmortem.json", help="Output file path (default: postmortem.json)")

    args = parser.parse_args()

    if args.command == "generate":
        generate_postmortem(args.run_dir, args.output)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
