#!/usr/bin/env python3
"""PRForge Memory Ledger — SQLite ledger over artifact trail.

Canonical truth is the enforced artifact trail (files on disk).
This ledger is the query index and manifest over those artifacts.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = os.environ.get("PRFORGE_MEMORY_DB", os.path.expanduser("~/.prforge/prforge_memory.db"))

def get_connection():
    """Open DB with WAL mode, busy_timeout, foreign_keys, NORMAL sync."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create all tables, indexes, and FTS5 virtual table."""
    conn = get_connection()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                issue_number INTEGER,
                pr_number INTEGER,
                branch TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                outcome TEXT,
                run_dir TEXT NOT NULL,
                tracking_enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS pr_events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                event_type TEXT NOT NULL,
                artifact_id TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
                FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
            );

            CREATE TABLE IF NOT EXISTS postmortems (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                repo TEXT NOT NULL,
                pr_number INTEGER,
                outcome TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                confidence TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS memory_records (
                id TEXT PRIMARY KEY,
                postmortem_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                lesson TEXT NOT NULL,
                lesson_type TEXT NOT NULL,
                lesson_fingerprint TEXT NOT NULL,
                scope_repo TEXT NOT NULL,
                scope_subsystem TEXT,
                scope_file_globs_json TEXT,
                evidence_artifact_ids_json TEXT NOT NULL,
                confidence TEXT NOT NULL,
                inferred INTEGER NOT NULL DEFAULT 0,
                promotion_state TEXT NOT NULL DEFAULT 'candidate',
                recurrence_count INTEGER NOT NULL DEFAULT 1,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                invalidated_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (postmortem_id) REFERENCES postmortems(id),
                FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                lesson,
                scope_repo,
                scope_subsystem,
                content='memory_records',
                content_rowid='rowid'
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_postmortems_run
                ON postmortems(run_id);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_artifacts_run_path
                ON artifacts(run_id, relative_path);

            CREATE INDEX IF NOT EXISTS idx_events_run_phase
                ON pr_events(run_id, phase, created_at);

            CREATE INDEX IF NOT EXISTS idx_memory_scope
                ON memory_records(scope_repo, scope_subsystem, promotion_state);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_fingerprint_scope
                ON memory_records(scope_repo, lesson_type, lesson_fingerprint);

            CREATE TRIGGER IF NOT EXISTS memory_records_ai AFTER INSERT ON memory_records BEGIN
                INSERT INTO memory_fts(rowid, lesson, scope_repo, scope_subsystem)
                VALUES (new.rowid, new.lesson, new.scope_repo, new.scope_subsystem);
            END;

            CREATE TRIGGER IF NOT EXISTS memory_records_ad AFTER DELETE ON memory_records BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, lesson, scope_repo, scope_subsystem)
                VALUES ('delete', old.rowid, old.lesson, old.scope_repo, old.scope_subsystem);
            END;

            CREATE TRIGGER IF NOT EXISTS memory_records_au AFTER UPDATE ON memory_records BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, lesson, scope_repo, scope_subsystem)
                VALUES ('delete', old.rowid, old.lesson, old.scope_repo, old.scope_subsystem);
                INSERT INTO memory_fts(rowid, lesson, scope_repo, scope_subsystem)
                VALUES (new.rowid, new.lesson, new.scope_repo, new.scope_subsystem);
            END;
        """)
    print(f"Database initialized at {DB_PATH}")

def sha256_file(path):
    """Compute SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()

def sha256_text(text):
    """Compute SHA256 of text."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]

def cmd_init(args):
    init_db()

def cmd_add_artifact(args):
    run_id = args.run_id
    path = args.path
    artifact_type = args.type
    run_dir = args.run_dir or os.environ.get("PRFORGE_RUN_DIR", "")

    if not os.path.isfile(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    # Compute relative path
    if run_dir and path.startswith(run_dir):
        relative = os.path.relpath(path, run_dir)
    else:
        relative = path

    sha = sha256_file(path)
    artifact_id = sha256_text(f"{run_id}:{relative}")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO artifacts (artifact_id, run_id, artifact_type, relative_path, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (artifact_id, run_id, artifact_type, relative, sha, now)
        )
    print(f"Artifact registered: {artifact_id} path={relative} sha256={sha}")

def cmd_append_event(args):
    run_id = args.run_id
    phase = args.phase
    event_type = args.type
    artifact_id = args.artifact_id or None
    payload = args.payload or "{}"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload_hash = sha256_text(payload)
    event_id = sha256_text(f"{run_id}:{phase}:{event_type}:{now}:{payload_hash}")

    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO pr_events (event_id, run_id, phase, event_type, artifact_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, run_id, phase, event_type, artifact_id, payload, now)
        )
    print(f"Event logged: {event_id} type={event_type} phase={phase}")

def cmd_save_postmortem(args):
    postmortem_id = args.id
    run_id = args.run_id
    repo = args.repo
    pr_number = args.pr_number
    outcome = args.outcome
    summary_json = args.summary
    evidence_json = args.evidence
    tags_json = args.tags
    confidence = args.confidence
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO postmortems (id, run_id, repo, pr_number, outcome, summary_json, evidence_json, tags_json, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (postmortem_id, run_id, repo, pr_number, outcome, summary_json, evidence_json, tags_json, confidence, now)
        )
    print(f"Postmortem saved: {postmortem_id}")

def cmd_search(args):
    query = args.query
    repo = args.repo or ""

    conn = get_connection()
    if repo:
        # Scoped search
        rows = conn.execute(
            "SELECT mr.id, mr.lesson, mr.scope_repo, mr.lesson_type, mr.promotion_state, mr.recurrence_count, mr.confidence FROM memory_records mr JOIN memory_fts ft ON mr.rowid = ft.rowid WHERE memory_fts MATCH ? AND mr.scope_repo = ? ORDER BY rank",
            (query, repo)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT mr.id, mr.lesson, mr.scope_repo, mr.lesson_type, mr.promotion_state, mr.recurrence_count, mr.confidence FROM memory_records mr JOIN memory_fts ft ON mr.rowid = ft.rowid WHERE memory_fts MATCH ? ORDER BY rank",
            (query,)
        ).fetchall()

    for row in rows:
        print(json.dumps(dict(row)))

def cmd_add_memory_record(args):
    """Add or update one memory lesson. Primarily used by tests and import tools."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lesson_fingerprint = sha256_text(args.lesson.lower().strip())
    record_id = args.id or hashlib.sha256(
        f"{args.postmortem_id}:{args.lesson_type}:{lesson_fingerprint}:{args.repo}".encode()
    ).hexdigest()
    evidence = json.loads(args.evidence_artifact_ids_json or "[]")
    file_globs = json.loads(args.file_globs_json or "[]")

    conn = get_connection()
    with conn:
        conn.execute("""
            INSERT INTO memory_records (
                id, postmortem_id, run_id, lesson, lesson_type,
                lesson_fingerprint, scope_repo, scope_subsystem,
                scope_file_globs_json, evidence_artifact_ids_json,
                confidence, inferred, promotion_state, recurrence_count,
                first_seen_at, last_seen_at, invalidated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope_repo, lesson_type, lesson_fingerprint) DO UPDATE SET
                lesson = excluded.lesson,
                scope_subsystem = excluded.scope_subsystem,
                scope_file_globs_json = excluded.scope_file_globs_json,
                evidence_artifact_ids_json = excluded.evidence_artifact_ids_json,
                confidence = excluded.confidence,
                inferred = excluded.inferred,
                promotion_state = excluded.promotion_state,
                recurrence_count = excluded.recurrence_count,
                last_seen_at = excluded.last_seen_at
        """, (
            record_id,
            args.postmortem_id,
            args.run_id,
            args.lesson,
            args.lesson_type,
            lesson_fingerprint,
            args.repo,
            args.subsystem,
            json.dumps(file_globs),
            json.dumps(evidence),
            args.confidence,
            1 if args.inferred else 0,
            args.promotion_state,
            args.recurrence_count,
            now,
            now,
            None,
            now,
        ))
    print(f"Memory record upserted: {record_id}")

def cmd_stats(args):
    conn = get_connection()
    stats = {}
    stats["runs"] = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
    stats["artifacts"] = conn.execute("SELECT count(*) FROM artifacts").fetchone()[0]
    stats["events"] = conn.execute("SELECT count(*) FROM pr_events").fetchone()[0]
    stats["postmortems"] = conn.execute("SELECT count(*) FROM postmortems").fetchone()[0]
    stats["memory_records"] = conn.execute("SELECT count(*) FROM memory_records").fetchone()[0]
    stats["memory_active"] = conn.execute("SELECT count(*) FROM memory_records WHERE promotion_state='active'").fetchone()[0]
    stats["memory_candidate"] = conn.execute("SELECT count(*) FROM memory_records WHERE promotion_state='candidate'").fetchone()[0]
    print(json.dumps(stats, indent=2))

def cmd_verify_artifacts(args):
    run_id = args.run_id
    conn = get_connection()
    rows = conn.execute("SELECT artifact_id, run_id, relative_path, sha256 FROM artifacts WHERE run_id=?", (run_id,)).fetchall()

    errors = 0
    run_dir_row = conn.execute("SELECT run_dir FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not run_dir_row:
        print(f"ERROR: run_id {run_id} not found", file=sys.stderr)
        sys.exit(1)

    run_dir = run_dir_row[0]

    for row in rows:
        artifact_id, rid, rel_path, stored_sha = row
        full_path = os.path.join(run_dir, rel_path)

        if not os.path.isfile(full_path):
            print(f"MISSING: {full_path} (artifact {artifact_id})")
            errors += 1
            continue

        current_sha = sha256_file(full_path)
        if current_sha != stored_sha:
            print(f"MISMATCH: {full_path} stored={stored_sha} current={current_sha}")
            errors += 1
            continue

    if errors:
        print(f"Verification FAILED: {errors} errors")
        sys.exit(1)
    else:
        print(f"Verification PASSED: {len(rows)} artifacts OK")

def cmd_rebuild_fts(args):
    conn = get_connection()
    with conn:
        conn.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
    print("FTS index rebuilt")

def cmd_retention(args):
    dry_run = args.dry_run
    days = args.days or 365
    conn = get_connection()

    # Find deletable artifacts (not referenced by non-invalidated memory records)
    deletable_artifacts = conn.execute("""
        SELECT a.artifact_id, a.relative_path FROM artifacts a
        LEFT JOIN memory_records mr ON mr.evidence_artifact_ids_json LIKE '%' || a.artifact_id || '%'
        LEFT JOIN postmortems p ON a.run_id = p.run_id
        WHERE mr.id IS NULL
    """).fetchall()

    print(f"Artifacts eligible for deletion: {len(deletable_artifacts)}")
    for row in deletable_artifacts:
        print(f"  {row[0]}: {row[1]}")

    if not dry_run:
        print("Execute mode not yet implemented")

def main():
    parser = argparse.ArgumentParser(description="PRForge Memory Ledger")
    subparsers = parser.add_subparsers(dest="command")

    # init
    subparsers.add_parser("init", help="Initialize database")

    # add-artifact
    p = subparsers.add_parser("add-artifact", help="Register an artifact")
    p.add_argument("--run-id", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--run-dir", default="")

    # append-event
    p = subparsers.add_parser("append-event", help="Log an event")
    p.add_argument("--run-id", required=True)
    p.add_argument("--phase", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--artifact-id", default="")
    p.add_argument("--payload", default="{}")

    # save-postmortem
    p = subparsers.add_parser("save-postmortem", help="Save a postmortem")
    p.add_argument("--id", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--pr-number", type=int, default=0)
    p.add_argument("--outcome", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--tags", required=True)
    p.add_argument("--confidence", required=True)

    # search
    p = subparsers.add_parser("search", help="Search memory records")
    p.add_argument("--query", required=True)
    p.add_argument("--repo", default="")

    # stats
    subparsers.add_parser("stats", help="Show database statistics")

    # verify-artifacts
    p = subparsers.add_parser("verify-artifacts", help="Verify artifact integrity")
    p.add_argument("--run-id", required=True)

    # rebuild-fts
    subparsers.add_parser("rebuild-fts", help="Rebuild FTS index")

    # add-memory-record
    p = subparsers.add_parser("add-memory-record", help="Add or update one memory lesson")
    p.add_argument("--id", default="")
    p.add_argument("--postmortem-id", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--lesson", required=True)
    p.add_argument("--lesson-type", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--subsystem", default="")
    p.add_argument("--file-globs-json", default="[]")
    p.add_argument("--evidence-artifact-ids-json", default="[]")
    p.add_argument("--confidence", default="high")
    p.add_argument("--promotion-state", default="active")
    p.add_argument("--recurrence-count", type=int, default=1)
    p.add_argument("--inferred", action="store_true")

    # retention
    p = subparsers.add_parser("retention", help="Retention policy check")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--days", type=int, default=365)

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "add-artifact":
        cmd_add_artifact(args)
    elif args.command == "append-event":
        cmd_append_event(args)
    elif args.command == "save-postmortem":
        cmd_save_postmortem(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "verify-artifacts":
        cmd_verify_artifacts(args)
    elif args.command == "rebuild-fts":
        cmd_rebuild_fts(args)
    elif args.command == "add-memory-record":
        cmd_add_memory_record(args)
    elif args.command == "retention":
        cmd_retention(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
