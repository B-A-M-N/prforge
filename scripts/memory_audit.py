#!/usr/bin/env python3
"""Memory audit script for PRForge.

Scans memory_records for issues and prints findings.
Exits nonzero if issues are found.

Usage:
    audit --min-confidence medium --format text
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any


def get_db_path() -> Path:
    """Determine the memory ledger database path."""
    prforge_home = Path.home() / ".prforge"
    db_path = prforge_home / "memory_ledger.db"
    return db_path


def load_all_records(db_path: Path) -> List[Dict[str, Any]]:
    """Load all memory records from the database."""
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM memory_records")
    rows = [dict(row) for row in cur.fetchall()]

    conn.close()
    return rows


def load_all_postmortems(db_path: Path) -> List[Dict[str, Any]]:
    """Load all postmortems from the database."""
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM postmortems")
    rows = [dict(row) for row in cur.fetchall()]

    conn.close()
    return rows


def load_all_artifacts(db_path: Path) -> List[Dict[str, Any]]:
    """Load all artifacts from the database."""
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM artifacts")
    rows = [dict(row) for row in cur.fetchall()]

    conn.close()
    return rows


def check_low_confidence_promoted(
    records: List[Dict[str, Any]], min_confidence: str
) -> List[Dict[str, Any]]:
    """Find records with low confidence promoted to active.

    Confidence hierarchy: low < medium < high
    """
    conf_order = {"low": 0, "medium": 1, "high": 2}
    min_level = conf_order.get(min_confidence, 1)

    issues = []
    for r in records:
        if r["promotion_state"] != "active":
            continue
        rec_conf = r["confidence"]
        if conf_order.get(rec_conf, 0) < min_level:
            issues.append(r)
    return issues


SCANNED_UNDER_MIN_RECURRENCE = "scanned_under_min_recurrence"

def check_inferred_low_recurrence(
    records: List[Dict[str, Any]], min_recurrence: int = 2
) -> List[Dict[str, Any]]:
    """Find inferred=1 records promoted to active with recurrence_count < min_recurrence.

    These should generally not be promoted to active unless they've been
    observed multiple times.
    """
    issues = []
    for r in records:
        if r["promotion_state"] != "active":
            continue
        if r["inferred"] and r["recurrence_count"] < min_recurrence:
            issues.append(r)
    return issues


def check_scope_creep(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Find universal claims (repo=None) based on single-PR evidence.

    A record with repo_scope=NULL (universal scope) but only 1-2 evidence
    references and recurrence_count=1 or 2 is suspicious.
    """
    issues = []
    for r in records:
        repo_scope = r.get("repo_scope")
        if repo_scope not in (None, "", "NULL"):
            continue
        if r["recurrence_count"] <= 2:
            evidence_refs = json.loads(r.get("evidence_refs_json", "[]") or "[]")
            if len(evidence_refs) <= 2:
                issues.append(r)
    return issues


def check_orphaned_records(
    records: List[Dict[str, Any]],
    postmortems: List[Dict[str, Any]],
    artifacts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Find records with no matching postmortems/artifacts.

    Checks that evidence_refs_json points to existing postmortems or artifacts.
    """
    pm_ids = {pm["run_id"] for pm in postmortems}
    artifact_ids = {a["artifact_id"] for a in artifacts}

    issues = []
    for r in records:
        evidence_refs = json.loads(r.get("evidence_refs_json", "[]") or "[]")
        if not evidence_refs:
            # No evidence at all - suspicious
            issues.append(r)
            continue

        found_match = False
        for ref in evidence_refs:
            ref_type = ref.get("type", "")
            ref_id = ref.get("ref_id", "")
            artifact_id = ref.get("artifact_id", "")

            if ref_type == "postmortem" or ref_type == "run":
                if ref_id in pm_ids:
                    found_match = True
                    break
            elif ref_type == "artifact":
                if artifact_id in artifact_ids or ref_id in artifact_ids:
                    found_match = True
                    break

        if not found_match:
            issues.append(r)
    return issues


def check_no_matching_artifacts(
    records: List[Dict[str, Any]],
    artifacts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Find records claiming artifact_ids that don't exist.

    Checks evidence_artifact_ids_json (if present) or artifact_id in evidence_refs.
    """
    artifact_ids = {a["artifact_id"] for a in artifacts}

    issues = []
    for r in records:
        evidence_refs = json.loads(r.get("evidence_refs_json", "[]") or "[]")
        for ref in evidence_refs:
            artifact_id = ref.get("artifact_id", "")
            if artifact_id and artifact_id not in artifact_ids:
                issues.append(r)
                break
    return issues


def confidence_label(level: str) -> str:
    colors = {"low": "\033[33m", "medium": "\033[33m", "high": "\033[32m"}
    reset = "\033[0m"
    if not sys.stdout.isatty():
        colors = {"low": "", "medium": "", "high": ""}
        reset = ""
    return f"{colors.get(level, '')}{level}{reset}"


def format_text_report(issues: Dict[str, List[Dict[str, Any]]]) -> str:
    """Format all issues as a text report."""
    lines = []
    lines.append("=" * 70)
    lines.append("MEMORY AUDIT REPORT")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)

    total_issues = sum(len(v) for v in issues.values())
    if total_issues == 0:
        lines.append("\n✓ No issues found.\n")
        return "\n".join(lines)

    lines.append(f"\n⚠ Found {total_issues} issue(s) across {len(issues)} category(ies)\n")

    category_labels = {
        "low_confidence_promoted": "Low confidence records promoted to active",
        "inferred_low_recurrence": "Inferred records with low recurrence promoted to active",
        "scope_creep": "Scope creep (universal claims from single-PR evidence)",
        "orphaned_records": "Orphaned records (no matching postmortems/artifacts)",
        "no_matching_artifacts": "Records with non-existent artifact references",
    }

    for cat_key, cat_issues in issues.items():
        if not cat_issues:
            continue
        label = category_labels.get(cat_key, cat_key)
        lines.append(f"--- {label} ({len(cat_issues)}) ---")
        for r in cat_issues:
            lines.append(f"  Record ID: {r['record_id']}")
            lines.append(f"    Fingerprint: {r['lesson_fingerprint']}")
            lesson = r["lesson_text"]
            if len(lesson) > 120:
                lesson = lesson[:117] + "..."
            lines.append(f"    Lesson: {lesson}")
            lines.append(f"    Type: {r['lesson_type']}")
            lines.append(f"    Confidence: {confidence_label(r['confidence'])}")
            lines.append(f"    Promotion: {r['promotion_state']}")
            lines.append(f"    Inferred: {bool(r['inferred'])}")
            lines.append(f"    Recurrence: {r['recurrence_count']}")
            repo_scope = r.get("repo_scope") or "(universal)"
            lines.append(f"    Scope: {repo_scope}")
            subsystem = r.get("subsystem") or "-"
            lines.append(f"    Subsystem: {subsystem}")
            evidence_refs = json.loads(r.get("evidence_refs_json", "[]") or "[]")
            lines.append(f"    Evidence refs: {len(evidence_refs)}")
            lines.append("")

    lines.append("=" * 70)
    lines.append(f"Total issues: {total_issues}")
    lines.append("=" * 70)
    return "\n".join(lines)


def format_json_report(issues: Dict[str, List[Dict[str, Any]]]) -> str:
    """Format issues as JSON."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "summary": {cat: len(iss) for cat, iss in issues.items()},
        "total_issues": sum(len(v) for v in issues.values()),
        "issues": issues,
    }
    return json.dumps(report, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(description="Audit memory records for issues.")
    parser.add_argument(
        "--min-confidence",
        choices=["low", "medium", "high"],
        default="medium",
        help="Minimum confidence for active records (default: medium)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    db_path = get_db_path()

    if not db_path.exists():
        print(f"No database found at {db_path}. Nothing to audit.", file=sys.stderr)
        sys.exit(0)

    records = load_all_records(db_path)
    postmortems = load_all_postmortems(db_path)
    artifacts = load_all_artifacts(db_path)

    issues = {
        "low_confidence_promoted": check_low_confidence_promoted(
            records, args.min_confidence
        ),
        "inferred_low_recurrence": check_inferred_low_recurrence(records),
        "scope_creep": check_scope_creep(records),
        "orphaned_records": check_orphaned_records(records, postmortems, artifacts),
        "no_matching_artifacts": check_no_matching_artifacts(records, artifacts),
    }

    if args.format == "json":
        print(format_json_report(issues))
    else:
        print(format_text_report(issues))

    total_issues = sum(len(v) for v in issues.values())
    if total_issues > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
