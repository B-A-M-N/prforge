#!/usr/bin/env python3
"""deterministic memory indexing and scoped recall regression."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
LEDGER = SCRIPTS / "memory_ledger.py"
INDEXER = SCRIPTS / "memory_indexer.py"
INJECTOR = SCRIPTS / "preflight_injector.py"

LESSON = "prefer targeted parser regression tests for malformed payload handling"


def run(cmd: list[str], env: dict[str, str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {' '.join(cmd)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def assert_fts5_available(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE VIRTUAL TABLE fts5_probe USING fts5(value)")
    except sqlite3.OperationalError as exc:
        raise AssertionError(f"sqlite fts5 unavailable: {exc}") from exc
    finally:
        conn.close()


def fetch_one(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> sqlite3.Row:
    row = conn.execute(sql, args).fetchone()
    if row is None:
        raise AssertionError(f"expected row for query: {sql}")
    return row


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="prforge-memory-regression.") as td:
        tmp = Path(td)
        db = tmp / "memory.db"
        run_dir = tmp / "run"
        artifacts = run_dir / "artifacts"
        artifacts.mkdir(parents=True)

        env = os.environ.copy()
        env["PRFORGE_MEMORY_DB"] = str(db)

        assert_fts5_available(db)
        run([sys.executable, str(LEDGER), "init"], env)

        run_id = "memory-regression-run"
        repo = "example/prforge"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute(
                "INSERT INTO runs (run_id, repo, started_at, run_dir) VALUES (?, ?, ?, ?)",
                (run_id, repo, "2026-05-07T00:00:00Z", str(run_dir)),
            )

        evidence_path = artifacts / "validation.txt"
        evidence_path.write_text("parser regression validation passed\n", encoding="utf-8")
        run(
            [
                sys.executable,
                str(LEDGER),
                "add-artifact",
                "--run-id",
                run_id,
                "--type",
                "validation",
                "--path",
                str(evidence_path),
                "--run-dir",
                str(run_dir),
            ],
            env,
        )

        artifact_id = fetch_one(
            conn,
            "SELECT artifact_id FROM artifacts WHERE run_id=? AND relative_path=?",
            (run_id, "artifacts/validation.txt"),
        )["artifact_id"]

        postmortem = {
            "id": "pm-memory-regression",
            "run_id": run_id,
            "repo": repo,
            "pr_number": 42,
            "outcome": "MERGED",
            "confidence": "high",
            "summary": {
                "what_worked": [
                    {
                        "lesson": LESSON,
                        "type": "what_worked",
                        "scope_subsystem": "src",
                        "file_globs": ["src/parser/*.py"],
                        "evidence": [{"artifact_id": artifact_id}],
                    }
                ],
                "test_expectations": [
                    {
                        "lesson": "skip malformed payload lessons when evidence is missing",
                        "type": "test_expectations",
                        "scope_subsystem": "src",
                        "file_globs": ["src/parser/*.py"],
                        "evidence": [{"artifact_id": "missing-artifact"}],
                    }
                ],
            },
            "evidence": {"artifact_ids": [artifact_id]},
            "tags": ["memory-regression"],
        }
        postmortem_path = run_dir / "postmortem.json"
        postmortem_path.write_text(json.dumps(postmortem, indent=2) + "\n", encoding="utf-8")

        index = [sys.executable, str(INDEXER), "index", "--postmortem", str(postmortem_path), "--run-dir", str(run_dir)]
        first = run(index, env)
        if "memory_count=1" not in first.stdout:
            raise AssertionError(f"expected exactly one indexed lesson\n{first.stdout}")

        rows = conn.execute(
            """
            SELECT lesson, scope_repo, scope_subsystem, scope_file_globs_json,
                   promotion_state, recurrence_count, evidence_artifact_ids_json
            FROM memory_records
            WHERE scope_repo=?
            """,
            (repo,),
        ).fetchall()
        if len(rows) != 1:
            raise AssertionError(f"expected one memory record, found {len(rows)}")

        record = rows[0]
        assert record["lesson"] == LESSON
        assert record["scope_subsystem"] == "src"
        assert json.loads(record["scope_file_globs_json"]) == ["src/parser/*.py"]
        assert record["promotion_state"] == "active"
        assert record["recurrence_count"] == 1
        assert json.loads(record["evidence_artifact_ids_json"]) == [artifact_id]

        fts_row = fetch_one(
            conn,
            """
            SELECT mr.lesson
            FROM memory_records mr
            JOIN memory_fts ft ON mr.rowid = ft.rowid
            WHERE memory_fts MATCH ?
            """,
            ("malformed",),
        )
        assert fts_row["lesson"] == LESSON

        recall = run(
            [
                sys.executable,
                str(INJECTOR),
                "--repo",
                repo,
                "--files",
                "src/parser/tokenizer.py",
                "--limit",
                "5",
            ],
            env,
        )
        if LESSON not in recall.stdout or "repo-scoped: example/prforge, subsystem src" not in recall.stdout:
            raise AssertionError(f"scoped recall did not return expected lesson\n{recall.stdout}")

        unrelated = run(
            [
                sys.executable,
                str(INJECTOR),
                "--repo",
                "other/repo",
                "--files",
                "src/parser/tokenizer.py",
                "--limit",
                "5",
            ],
            env,
        )
        if unrelated.stdout.strip():
            raise AssertionError(f"unexpected cross-repo recall output\n{unrelated.stdout}")

        second = run(index, env)
        if "memory_count=1" not in second.stdout:
            raise AssertionError(f"expected repeated indexing to update one record\n{second.stdout}")
        count, recurrence = fetch_one(
            conn,
            "SELECT count(*) AS c, max(recurrence_count) AS r FROM memory_records WHERE scope_repo=?",
            (repo,),
        )
        assert count == 1
        assert recurrence == 2

        malformed = tmp / "malformed-postmortem.json"
        malformed.write_text('{"id": "bad", "summary": ', encoding="utf-8")
        bad = run(
            [sys.executable, str(INDEXER), "index", "--postmortem", str(malformed), "--run-dir", str(run_dir)],
            env,
            check=False,
        )
        if bad.returncode == 0:
            raise AssertionError("malformed postmortem unexpectedly indexed successfully")

    print("memory indexing regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
