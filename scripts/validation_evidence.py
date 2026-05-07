#!/usr/bin/env python3
"""Verify PRForge validation claims against captured command evidence."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


DEFAULT_DB = Path(os.environ.get("PRFORGE_MEMORY_DB", "~/.prforge/prforge_memory.db")).expanduser()


def normalize_command(command: str) -> str:
    return " ".join((command or "").strip().split())


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def run_id_from_state(state: dict, artifact_dir: Path) -> str:
    return (
        state.get("memory_context", {}).get("memory_run_id")
        or state.get("run_id")
        or artifact_dir.name
    )


def command_events(db_path: Path, run_id: str) -> list[dict]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT event_type, payload_json, created_at
            FROM pr_events
            WHERE run_id = ?
              AND event_type IN ('bash_command_result', 'bash_command')
            ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    events: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        payload["_event_type"] = row["event_type"]
        payload["_created_at"] = row["created_at"]
        payload["_normalized_command"] = normalize_command(payload.get("command", ""))
        events.append(payload)
    return events


def verify(artifact_dir: Path, state_file: Path | None = None, db_path: Path = DEFAULT_DB) -> tuple[bool, list[str]]:
    state_path = state_file or artifact_dir / "state.json"
    issues: list[str] = []
    try:
        state = load_json(state_path)
    except Exception as exc:
        return False, [f"state.json unreadable: {exc}"]

    validation = state.get("validation") or {}
    commands_run = validation.get("commands_run") or []
    commands_not_run = validation.get("commands_not_run") or []

    if not commands_run:
        issues.append("validation.commands_run is empty")

    failed = [c.get("command", "?") for c in commands_run if c.get("status") != "passed"]
    if failed:
        issues.append("validation.commands_run contains non-passing entries: " + ", ".join(failed[:5]))

    if commands_not_run:
        issues.append(
            "validation.commands_not_run is not empty: "
            + ", ".join((c.get("command", "?") for c in commands_not_run[:5]))
        )

    ledger = artifact_dir / "validation_ledger.md"
    if not ledger.exists():
        issues.append("validation_ledger.md missing")
    else:
        text = ledger.read_text(encoding="utf-8", errors="replace").strip()
        if len(text) < 50:
            issues.append("validation_ledger.md is too short to be evidence")

    run_id = run_id_from_state(state, artifact_dir)
    events = command_events(db_path, run_id)
    result_events = [e for e in events if e.get("_event_type") == "bash_command_result"]
    result_by_command: dict[str, list[dict]] = {}
    for event in result_events:
        result_by_command.setdefault(event["_normalized_command"], []).append(event)

    if commands_run and not result_events:
        issues.append(f"no bash_command_result evidence found in memory ledger for run_id={run_id}")

    for claim in commands_run:
        command = normalize_command(claim.get("command", ""))
        if not command:
            issues.append("validation.commands_run contains an empty command")
            continue
        matches = result_by_command.get(command, [])
        if not matches:
            issues.append(f"no command evidence for claimed validation: {command}")
            continue
        passing = [
            e for e in matches
            if str(e.get("exit_code", e.get("rc", ""))) == "0"
            or e.get("status") == "passed"
        ]
        if not passing:
            issues.append(f"claimed validation did not have a passing captured result: {command}")

    return not issues, issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify validation claims against command evidence")
    parser.add_argument("artifact_dir")
    parser.add_argument("--state-file", default="")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    ok, issues = verify(
        Path(args.artifact_dir),
        Path(args.state_file) if args.state_file else None,
        Path(args.db).expanduser(),
    )
    if args.json:
        print(json.dumps({"ok": ok, "issues": issues}, indent=2))
    elif ok:
        print("OK")
    else:
        print("FAIL:" + " | ".join(issues))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
