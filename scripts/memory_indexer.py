#!/usr/bin/env python3
"""Memory Indexer — creates scoped memory records from postmortem.json.

Handles deduplication and evidence verification for memory records
derived from postmortem analysis.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from memory_ledger import get_connection, sha256_text, DB_PATH


def artifact_exists(conn, artifact_id):
    """Check if an artifact_id exists in the artifacts table."""
    row = conn.execute(
        "SELECT 1 FROM artifacts WHERE artifact_id = ?",
        (artifact_id,)
    ).fetchone()
    return row is not None


def extract_evidence_refs(evidence_json_str):
    """Extract artifact IDs referenced in evidence_json.

    Returns a list of artifact_id strings.
    """
    try:
        evidence = json.loads(evidence_json_str)
    except (json.JSONDecodeError, TypeError):
        return []

    artifact_ids = []

    # Handle list of artifact IDs
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, str):
                artifact_ids.append(item)
            elif isinstance(item, dict) and "artifact_id" in item:
                artifact_ids.append(item["artifact_id"])
    # Handle dict with evidence_refs or artifact_ids key
    elif isinstance(evidence, dict):
        if "evidence_refs" in evidence:
            refs = evidence["evidence_refs"]
            if isinstance(refs, list):
                for item in refs:
                    if isinstance(item, str):
                        artifact_ids.append(item)
                    elif isinstance(item, dict) and "artifact_id" in item:
                        artifact_ids.append(item["artifact_id"])
        if "artifact_ids" in evidence:
            aids = evidence["artifact_ids"]
            if isinstance(aids, list):
                for item in aids:
                    if isinstance(item, str):
                        artifact_ids.append(item)
        # Direct artifact_id
        if "artifact_id" in evidence:
            artifact_ids.append(evidence["artifact_id"])

    return artifact_ids


def get_postmortem_summary(postmortem_json_str):
    """Extract summary arrays from postmortem JSON.

    Returns dict with keys: what_was_done, could_be_better, avoid_next_time,
    maintainer_preferences, and other category -> list of lesson texts.
    Each entry is (lesson_type, lesson_text, evidence_refs, subsystem, file_globs).
    """
    try:
        pm = json.loads(postmortem_json_str)
    except (json.JSONDecodeError, TypeError):
        return {}

    summary = pm.get("summary", {})
    if not isinstance(summary, dict):
        return {}
    evidence_json = pm.get("evidence", {})

    # Evidence refs by category
    evidence_map = {}
    if isinstance(evidence_json, dict):
        for key, val in evidence_json.items():
            evidence_map[key] = extract_evidence_refs(json.dumps(val))

    results = {}

    # Standard categories
    categories = [
        "what_was_done",
        "what_worked",
        "could_be_better",
        "avoid_next_time",
        "maintainer_preferences",
        "repo_conventions",
        "reviewer_objections",
        "test_expectations",
        "lessons_learned",
        "recommendations",
    ]

    for category in categories:
        items = summary.get(category, [])
        if not items:
            continue

        typed_items = []
        for item in items:
            if isinstance(item, str):
                lesson_text = item
                lesson_type = category
                ev_refs = evidence_map.get(category, [])
                subsystem = ""
                file_globs = []
            elif isinstance(item, dict):
                lesson_text = item.get("text", item.get("lesson", ""))
                lesson_type = item.get("type", category)
                ev_refs = extract_evidence_refs(json.dumps(item.get("evidence", [])))
                if not ev_refs:
                    ev_refs = evidence_map.get(category, [])
                subsystem = item.get("scope_subsystem") or item.get("subsystem") or ""
                file_globs = item.get("file_globs") or item.get("scope_file_globs") or []
                if isinstance(file_globs, str):
                    file_globs = [file_globs]
            else:
                continue

            if lesson_text:
                typed_items.append((
                    lesson_type,
                    lesson_text.strip(),
                    ev_refs,
                    subsystem if isinstance(subsystem, str) else "",
                    file_globs if isinstance(file_globs, list) else [],
                ))

        if typed_items:
            results[category] = typed_items

    return results


def compute_lesson_fingerprint(lesson_text):
    """Compute lesson fingerprint: sha256(lesson.lower().strip())[:16]."""
    return sha256_text(lesson_text.lower().strip())


def needs_promotion(lesson_type, confidence, recurrence_count, inferred, is_repo_scoped, is_directly_evidenced):
    """Determine if a lesson should be promoted to 'active'.

    Returns True if promotion to 'active' is warranted.
    """
    # High confidence, directly evidenced, repo-scoped lessons from
    # certain types can be active with just one PR
    if confidence == "high" and is_directly_evidenced:
        if is_repo_scoped and lesson_type in ["what_worked", "could_be_better", "avoid_next_time"]:
            return True

    # maintainer_preference, repo_convention, reviewer_objection,
    # test_expectation: require recurrence_count>=2 AND confidence=high
    if lesson_type in ["maintainer_preferences", "repo_conventions",
                       "reviewer_objections", "test_expectations"]:
        if recurrence_count >= 2 and confidence == "high":
            return True
        return False

    # Global lessons (no repo scope): require recurrence_count>=2 AND already active
    # But this is checked at the database level — we return False here
    # and let the existing record's state persist
    if not is_repo_scoped:
        # For global lessons, only promote if already recurring enough
        if recurrence_count >= 2 and confidence == "high":
            return True
        return False

    # Regular repo-scoped lessons
    if recurrence_count >= 2 and confidence == "high":
        return True

    return False


def cmd_index(args):
    """Index a postmortem.json into memory records."""
    postmortem_path = args.postmortem
    run_dir = args.run_dir

    if not os.path.isfile(postmortem_path):
        print(f"ERROR: postmortem.json not found: {postmortem_path}", file=sys.stderr)
        sys.exit(1)

    # Read postmortem.json
    with open(postmortem_path, 'r') as f:
        postmortem_data = json.load(f)

    # Extract postmortem metadata
    run_id = postmortem_data.get("run_id", "")
    if not run_id and run_dir:
        run_id = os.path.basename(os.path.abspath(run_dir))

    if not run_id:
        print("ERROR: Could not determine run_id from postmortem or run_dir", file=sys.stderr)
        sys.exit(1)

    postmortem_id = postmortem_data.get("id") or f"{run_id}-postmortem"

    repo_raw = postmortem_data.get("repo", "")
    if isinstance(repo_raw, dict):
        # postmortem was written with an object-form repo (pre-fix generator output)
        repo = (repo_raw.get("github")
                or os.path.basename((repo_raw.get("local_path") or "").rstrip("/"))
                or "")
    else:
        repo = str(repo_raw) if repo_raw else ""
    confidence = postmortem_data.get("confidence", "medium")

    # Step: Verify artifacts before indexing
    print(f"Verifying artifacts for run {run_id}...")
    conn = get_connection()

    # Call memory_ledger.py verify-artifacts directly
    ledger_path = os.path.join(os.path.dirname(__file__), "memory_ledger.py")
    result = subprocess.run(
        [sys.executable, ledger_path, "verify-artifacts", "--run-id", run_id],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print("WARNING: Artifact verification failed. Skipping memory promotion for this postmortem.")
        # But we still want to index lessons that don't require evidence
        # Continue but mark as inferred=1
    else:
        print("Artifact verification PASSED.")

    # Get all artifact IDs for this run
    artifact_rows = conn.execute(
        "SELECT artifact_id FROM artifacts WHERE run_id = ?",
        (run_id,)
    ).fetchall()
    existing_artifact_ids = {row[0] for row in artifact_rows}

    # Extract summary arrays
    summary = get_postmortem_summary(json.dumps(postmortem_data))

    memory_count = 0
    lessons_processed = 0

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Seed FK parent run row so postmortem insert does not violate FK constraint
    conn.execute("""
        INSERT OR IGNORE INTO runs (run_id, repo, started_at, run_dir)
        VALUES (?, ?, ?, ?)
    """, (run_id, repo, now, run_dir or ""))

    conn.execute("""
        INSERT OR IGNORE INTO postmortems (id, run_id, repo, pr_number, outcome,
            summary_json, evidence_json, tags_json, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        postmortem_id, run_id, repo, postmortem_data.get("pr_number", 0),
        postmortem_data.get("outcome", ""),
        json.dumps(postmortem_data.get("summary", {})),
        json.dumps(postmortem_data.get("evidence", {})),
        json.dumps(postmortem_data.get("tags", [])),
        confidence, now
    ))
    conn.commit()

    # Process each category
    for category, items in summary.items():
        for item in items:
            if len(item) == 3:
                lesson_type, lesson_text, evidence_refs = item
                scope_subsystem = ""
                file_globs = []
            else:
                lesson_type, lesson_text, evidence_refs, scope_subsystem, file_globs = item
            lessons_processed += 1

            lesson_fingerprint = compute_lesson_fingerprint(lesson_text)

            # Check evidence refs exist
            valid_artifact_refs = []
            has_invalid_evidence = False
            for aid in evidence_refs:
                if aid in existing_artifact_ids:
                    valid_artifact_refs.append(aid)
                else:
                    print(f"  WARNING: Evidence artifact {aid} not found for lesson: {lesson_text[:60]}...")
                    has_invalid_evidence = True

            # Skip if no valid evidence and evidence was referenced
            if evidence_refs and not valid_artifact_refs:
                print(f"  SKIP: No valid evidence found for lesson: {lesson_text[:60]}...")
                continue

            is_directly_evidenced = len(valid_artifact_refs) > 0
            is_repo_scoped = bool(repo)

            # Check for existing record with same (scope_repo, lesson_type, lesson_fingerprint)
            existing = conn.execute("""
                SELECT id, recurrence_count, promotion_state, inferred, evidence_artifact_ids_json
                FROM memory_records
                WHERE scope_repo = ? AND lesson_type = ? AND lesson_fingerprint = ?
            """, (repo or "", lesson_type, lesson_fingerprint)).fetchone()

            if existing:
                # Update existing record
                record_id = existing["id"]
                old_count = existing["recurrence_count"]
                new_count = old_count + 1

                # Determine if promotion is warranted
                should_promote = needs_promotion(
                    lesson_type, confidence, new_count, existing["inferred"],
                    is_repo_scoped, is_directly_evidenced
                )
                new_state = "active" if should_promote else existing["promotion_state"]

                # Merge evidence IDs
                all_evidence = set(valid_artifact_refs)
                if existing["evidence_artifact_ids_json"]:
                    old_evidence = json.loads(existing["evidence_artifact_ids_json"])
                    all_evidence.update(old_evidence)

                conn.execute("""
                    UPDATE memory_records
                    SET recurrence_count = ?,
                        last_seen_at = ?,
                        promotion_state = ?,
                        evidence_artifact_ids_json = ?,
                        scope_subsystem = ?,
                        scope_file_globs_json = ?
                    WHERE id = ?
                """, (
                    new_count, now, new_state, json.dumps(sorted(all_evidence)),
                    scope_subsystem, json.dumps(file_globs), record_id
                ))

                print(f"  UPDATED: lesson (recurrence {old_count} -> {new_count}), state={new_state}")
                memory_count += 1
            else:
                # Determine initial promotion state
                should_promote = needs_promotion(
                    lesson_type, confidence, 1, 0,
                    is_repo_scoped, is_directly_evidenced
                )

                if is_repo_scoped:
                    if lesson_type in ["what_worked", "could_be_better", "avoid_next_time"]:
                        if confidence == "high" and is_directly_evidenced:
                            initial_state = "active"
                        else:
                            initial_state = "candidate"
                    elif lesson_type in ["maintainer_preferences", "repo_conventions",
                                         "reviewer_objections", "test_expectations"]:
                        if confidence == "high":
                            initial_state = "inferred"
                        else:
                            initial_state = "candidate"
                    else:
                        initial_state = "candidate"
                else:
                    # Global (no repo scope)
                    initial_state = "global"

                if should_promote:
                    initial_state = "active"

                record_id = hashlib.sha256(
                    f"{postmortem_id}:{lesson_type}:{lesson_fingerprint}".encode()
                ).hexdigest()

                conn.execute("""
                    INSERT INTO memory_records (
                        id, postmortem_id, run_id, lesson, lesson_type,
                        lesson_fingerprint, scope_repo, scope_subsystem,
                        scope_file_globs_json, evidence_artifact_ids_json,
                        confidence, inferred, promotion_state, recurrence_count,
                        first_seen_at, last_seen_at, invalidated_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record_id, postmortem_id, run_id, lesson_text, lesson_type,
                    lesson_fingerprint, repo or "", scope_subsystem,
                    json.dumps(file_globs), json.dumps(sorted(valid_artifact_refs)),
                    confidence, 0 if should_promote else 1, initial_state,
                    1, now, now, None, now
                ))

                print(f"  NEW: lesson_type={lesson_type}, state={initial_state}, evidence={len(valid_artifact_refs)}")
                memory_count += 1

    conn.commit()

    # Save postmortem record if not exists
    pm_existing = conn.execute(
        "SELECT id FROM postmortems WHERE id = ?",
        (postmortem_id,)
    ).fetchone()

    if not pm_existing:
        conn.execute("""
            INSERT INTO postmortems (id, run_id, repo, pr_number, outcome,
                summary_json, evidence_json, tags_json, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            postmortem_id, run_id, repo, postmortem_data.get("pr_number", 0),
            postmortem_data.get("outcome", ""),
            json.dumps(postmortem_data.get("summary", {})),
            json.dumps(postmortem_data.get("evidence", {})),
            json.dumps(postmortem_data.get("tags", [])),
            confidence, now
        ))
        conn.commit()

    conn.close()

    # Mandatory: rebuild FTS
    print("\nRebuilding FTS index...")
    ledger_path = os.path.join(os.path.dirname(__file__), "memory_ledger.py")
    subprocess.run(
        [sys.executable, ledger_path, "rebuild-fts"],
        capture_output=True
    )

    if memory_count == 0:
        print("\nNo extractable lessons from this PR")
        print(f"memory_count=0")
    else:
        print(f"\nIndexed {memory_count} memory record(s) from {lessons_processed} lessons")
        print(f"memory_count={memory_count}")


def cmd_query(args):
    """Query memory records."""
    conn = get_connection()

    if args.repo:
        # Scoped search
        rows = conn.execute("""
            SELECT mr.id, mr.lesson, mr.scope_repo, mr.lesson_type,
                   mr.confidence, mr.promotion_state, mr.recurrence_count,
                   mr.first_seen_at, mr.last_seen_at, mr.inferred,
                   mr.evidence_artifact_ids_json
            FROM memory_records mr
            JOIN memory_fts ft ON mr.rowid = ft.rowid
            WHERE memory_fts MATCH ? AND mr.scope_repo = ?
            ORDER BY rank
            LIMIT ?
        """, (args.query, args.repo, args.limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT mr.id, mr.lesson, mr.scope_repo, mr.lesson_type,
                   mr.confidence, mr.promotion_state, mr.recurrence_count,
                   mr.first_seen_at, mr.last_seen_at, mr.inferred,
                   mr.evidence_artifact_ids_json
            FROM memory_records mr
            JOIN memory_fts ft ON mr.rowid = ft.rowid
            WHERE memory_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (args.query, args.limit)).fetchall()

    conn.close()

    if not rows:
        print("No results found.")
        return

    for row in rows:
        evidence = json.loads(row["evidence_artifact_ids_json"]) if row["evidence_artifact_ids_json"] else []
        print(json.dumps({
            "id": row["id"],
            "lesson": row["lesson"],
            "scope_repo": row["scope_repo"],
            "lesson_type": row["lesson_type"],
            "confidence": row["confidence"],
            "promotion_state": row["promotion_state"],
            "recurrence_count": row["recurrence_count"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "inferred": row["inferred"],
            "evidence_artifact_ids": evidence,
        }, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Memory Indexer — create scoped memory records from postmortems"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # index command
    p_index = subparsers.add_parser(
        "index",
        help="Index a postmortem.json into memory records"
    )
    p_index.add_argument(
        "--postmortem",
        required=True,
        help="Path to postmortem.json file"
    )
    p_index.add_argument(
        "--run-dir",
        required=True,
        help="Path to the run directory"
    )
    p_index.set_defaults(func=cmd_index)

    # query command
    p_query = subparsers.add_parser(
        "query",
        help="Query memory records"
    )
    p_query.add_argument(
        "--query",
        required=True,
        help="Search query text"
    )
    p_query.add_argument(
        "--repo",
        default="",
        help="Scope results to this repo (org/repo)"
    )
    p_query.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of results (default: 10)"
    )
    p_query.set_defaults(func=cmd_query)

    # Import the base memory_ledger commands too (verify-artifacts, rebuild-fts, etc.)
    # Pass through to parent script
    for cmd_name in ["verify-artifacts", "rebuild-fts", "init", "stats",
                     "search", "add-artifact", "append-event", "save-postmortem",
                     "retention"]:
        sub = subparsers.add_parser(cmd_name, help=f"memory_ledger.py {cmd_name}")

    args = parser.parse_args()

    if args.command == "index":
        cmd_index(args)
    elif args.command == "query":
        cmd_query(args)
    elif args.command in ["verify-artifacts", "rebuild-fts", "init", "stats",
                          "search", "add-artifact", "append-event", "save-postmortem",
                          "retention"]:
        # Delegate to memory_ledger.py
        subprocess.run([sys.executable, __file__.replace("memory_indexer.py", "memory_ledger.py")] + sys.argv[1:])
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
