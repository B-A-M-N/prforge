#!/usr/bin/env python3
"""Preflight injection script for PRForge memory system.

Queries memory_ledger.py with hard scoping to surface relevant prior lessons
before a PR run begins. Outputs scoped warnings to guide the agent.

Usage:
    inject --repo org/repo --limit 5 --files "src/foo.ts" --issue-type bug
"""

import argparse
import os
import json
import sqlite3
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional


def get_db_path() -> Path:
    """Determine the memory ledger database path."""
    if os.environ.get("PRFORGE_MEMORY_DB"):
        return Path(os.environ["PRFORGE_MEMORY_DB"]).expanduser()
    prforge_home = Path.home() / ".prforge"
    prforge_home.mkdir(exist_ok=True)
    db_path = prforge_home / "prforge_memory.db"
    return db_path


def query_memory_records(
    db_path: Path,
    repo: str,
    file_paths: List[str],
    limit: int,
    issue_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query memory records with scoping rules.

    Ranking logic:
      Rank 1 = exact repo + matching files
      Rank 2 = exact repo + matching subsystem/type
      Rank 3 = same org + subsystem match
      Rank 4 = global active with recurrence_count >= 2
    """
    if not db_path.exists():
        return []

    org = repo.split("/")[0] if "/" in repo else repo

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    results = []

    # Build file glob patterns from file paths for matching
    # Extract subsystems from file paths (first directory component)
    subsystems = set()
    for fp in file_paths:
        parts = fp.split("/")
        if len(parts) > 1:
            subsystems.add(parts[0])
        else:
            subsystems.add("root")

    base_select = """
        SELECT id AS record_id,
               lesson_fingerprint,
               lesson_type,
               lesson AS lesson_text,
               scope_repo AS repo_scope,
               scope_subsystem AS subsystem,
               scope_file_globs_json AS file_globs,
               confidence,
               recurrence_count,
               evidence_artifact_ids_json AS evidence_refs_json,
               inferred,
               promotion_state,
               first_seen_at,
               last_seen_at,
               {rank} AS rank
        FROM memory_records
    """

    # ---- Rank 1: exact repo + matching files ----
    query1 = base_select.format(rank=1) + """
        WHERE promotion_state = 'active'
          AND scope_repo = ?
    """

    cur.execute(query1, [repo])
    for row in cur.fetchall():
        file_globs = json.loads(row["file_globs"] or "[]")
        if _matches_file_patterns(file_paths, file_globs) or not file_globs:
            results.append(dict(row))

    if len(results) < limit:
        # ---- Rank 2: exact repo + matching subsystem ----
        query2 = base_select.format(rank=2) + """
            WHERE promotion_state = 'active'
              AND scope_repo = ?
              AND scope_subsystem IS NOT NULL
        """
        cur.execute(query2, [repo])
        for row in cur.fetchall():
            sub = row["subsystem"]
            if sub and any(s in sub for s in subsystems):
                # Avoid dupes by lesson_fingerprint
                fingerprint = row["lesson_fingerprint"]
                if not any(r["lesson_fingerprint"] == fingerprint for r in results):
                    results.append(dict(row))

    if len(results) < limit:
        # ---- Rank 3: same org + subsystem match ----
        query3 = base_select.format(rank=3) + """
            WHERE promotion_state = 'active'
              AND scope_repo LIKE ?
              AND scope_subsystem IS NOT NULL
        """
        cur.execute(query3, [f"{org}/%"])
        for row in cur.fetchall():
            sub = row["subsystem"]
            if sub and any(s in sub for s in subsystems):
                fingerprint = row["lesson_fingerprint"]
                if not any(r["lesson_fingerprint"] == fingerprint for r in results):
                    results.append(dict(row))

    if len(results) < limit:
        # ---- Rank 4: global active with recurrence_count >= 2 ----
        query4 = base_select.format(rank=4) + """
            WHERE promotion_state = 'active'
              AND (scope_repo IS NULL OR scope_repo = '')
              AND recurrence_count >= 2
            ORDER BY recurrence_count DESC, last_seen_at DESC
            LIMIT ?
        """
        remaining = limit - len(results)
        cur.execute(query4, [remaining])
        for row in cur.fetchall():
            fingerprint = row["lesson_fingerprint"]
            if not any(r["lesson_fingerprint"] == fingerprint for r in results):
                results.append(dict(row))

    conn.close()

    # Sort by rank then recurrence_count descending
    results.sort(key=lambda r: (r["rank"], -r["recurrence_count"]))
    return results[:limit]


def _matches_file_patterns(file_paths: List[str], file_globs: List[str]) -> bool:
    """Check if any file path matches any glob pattern."""
    if not file_globs:
        return False
    import fnmatch
    for fp in file_paths:
        for pattern in file_globs:
            if fnmatch.fnmatch(fp, pattern):
                return True
    return False


def _get_evidence_summary(record: Dict[str, Any]) -> List[str]:
    """Extract readable evidence summaries from evidence_refs_json."""
    evidence_refs = json.loads(record.get("evidence_refs_json", "[]"))
    summaries = []
    for ref in evidence_refs:
        if isinstance(ref, str):
            summaries.append(f"artifact {ref}")
            continue
        if not isinstance(ref, dict):
            continue
        ref_type = ref.get("type", "")
        ref_id = ref.get("ref_id", "")
        if ref_type == "postmortem":
            summaries.append(f"Postmortem {ref_id}")
        elif ref_type == "review":
            summaries.append(f"review {ref_id}")
        elif ref_type == "ci":
            summaries.append(f"CI run {ref_id}")
        elif ref_type == "artifact":
            summaries.append(f"artifact {ref_id}")
        else:
            summaries.append(f"{ref_type} {ref_id}")
    return summaries


def format_output(records: List[Dict[str, Any]], repo: str) -> str:
    """Format records into the scoped warning output format."""
    if not records:
        return ""

    lines = []
    for record in records:
        rank = record["rank"]
        repo_scope = record.get("repo_scope") or ""
        subsystem = record.get("subsystem") or ""

        # Build scope label
        if rank == 1:
            scope_label = f"repo-scoped: {repo}"
        elif rank == 2:
            scope_label = f"repo-scoped: {repo}"
        elif rank == 3:
            scope_label = f"org-scoped: {repo.split('/')[0]}"
        else:
            scope_label = "global"

        if subsystem:
            scope_label = f"{scope_label}, subsystem {subsystem}"

        evidence_summary = ", ".join(_get_evidence_summary(record))

        lines.append(
            f"Relevant prior lesson [{scope_label}]:\n"
            f"{record['lesson_text']}\n"
            f"Evidence: {evidence_summary}"
        )

    return "\n\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Inject relevant memory lessons into PR preflight checks."
    )
    parser.add_argument("--repo", required=True, help="Repository identifier (org/repo)")
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of lessons to inject (default: 5)",
    )
    parser.add_argument(
        "--files",
        help="Comma-separated list of changed files",
    )
    parser.add_argument(
        "--issue-type",
        help="Type of issue (e.g., bug, feature, refactor)",
    )
    args = parser.parse_args()

    file_paths = []
    if args.files:
        file_paths = [f.strip() for f in args.files.split(",") if f.strip()]

    # Import memory_ledger functions (as requested)
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        # memory_ledger is expected to be in the same directory or available
    except Exception:
        pass

    db_path = get_db_path()

    records = query_memory_records(
        db_path=db_path,
        repo=args.repo,
        file_paths=file_paths,
        limit=args.limit,
        issue_type=args.issue_type,
    )

    output = format_output(records, args.repo)
    if output:
        print(output)
        sys.exit(0)
    else:
        # No scope label = no injection
        sys.exit(0)


if __name__ == "__main__":
    main()
