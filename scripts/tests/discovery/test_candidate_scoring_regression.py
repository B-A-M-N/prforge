#!/usr/bin/env python3
"""deterministic candidate discovery scoring regression."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCORER = ROOT / "scripts" / "candidate_discovery.py"


def run_scorer(candidates: list[dict]) -> dict:
    with tempfile.TemporaryDirectory(prefix="prforge-candidate-regression.") as td:
        path = Path(td) / "candidates.json"
        path.write_text(json.dumps(candidates, indent=2) + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCORER), str(path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"candidate scorer failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return json.loads(result.stdout)


def by_number(result: dict, number: int) -> dict:
    for item in result["candidates"]:
        if item["number"] == number:
            return item
    raise AssertionError(f"candidate #{number} missing from result")


def main() -> int:
    candidates = [
        {
            "number": 101,
            "title": "fix parser crash on malformed payload",
            "body": "steps to reproduce included. expected no crash, actual crash. existing tests cover parser.",
            "labels": [{"name": "bug"}, {"name": "good first issue"}],
            "assignees": [],
            "comments": [
                {
                    "author": {"login": "maintainer"},
                    "author_association": "MEMBER",
                    "body": "confirmed valid, please send a pr with a regression test.",
                }
            ],
            "maintainers": ["maintainer"],
            "tests_available": True,
            "reproducible": True,
            "recent_merged_prs": 8,
            "age_days": 12,
            "url": "https://github.com/example/repo/issues/101",
        },
        {
            "number": 102,
            "title": "rewrite the plugin architecture",
            "body": "large refactor across the whole system and many files.",
            "labels": ["refactor", "architecture"],
            "assignees": [],
            "comments": [],
            "age_days": 30,
        },
        {
            "number": 103,
            "title": "fix typo in config warning",
            "body": "small docs-ish fix. i'll work on this.",
            "labels": ["help wanted"],
            "assignees": [{"login": "someone"}],
            "comments": [],
            "tests_available": True,
            "age_days": 4,
        },
        {
            "number": 104,
            "title": "old flaky issue with no repro",
            "body": "maybe flaky.",
            "labels": ["bug"],
            "assignees": [],
            "comments": [],
            "age_days": 900,
        },
        {
            "number": 105,
            "title": "fix oauth token refresh race",
            "body": "clear repro and tests exist, but touches auth token credential handling.",
            "labels": ["bug", "auth"],
            "assignees": [],
            "comments": [
                {
                    "author": {"login": "maintainer"},
                    "author_association": "MEMBER",
                    "body": "confirmed valid.",
                }
            ],
            "maintainers": ["maintainer"],
            "tests_available": True,
            "reproducible": True,
            "recent_merged_prs": 6,
            "age_days": 8,
        },
        {
            "number": 106,
            "title": "fix parser error already covered elsewhere",
            "body": "duplicate of #44 and already fixed by a pending branch.",
            "labels": ["bug", "duplicate"],
            "assignees": [],
            "comments": [],
            "tests_available": True,
            "age_days": 1,
        },
    ]

    result = run_scorer(candidates)
    assert result["status"] == "ranked"
    ordered = [item["number"] for item in result["candidates"]]
    assert ordered[0] == 101, ordered

    best = by_number(result, 101)
    assert best["recommendation"] == "best"
    assert best["testable"] is True
    assert best["maintainer_confirmed"] is True
    assert any("locally testable" in r for r in best["reasons"])
    assert any("maintainer confirmed" in r for r in best["reasons"])
    assert best["risk_level"] == "low"
    assert best["reason_summary"]
    assert best["testability_signal"] == "strong"
    assert best["maintainer_signal"] == "confirmed"
    assert best["scope_size_signal"] == "small"
    assert best["claimed_duplicate_stale_signal"] == "clear"
    assert "parser" in best["subsystems"]
    assert "src/parser/" in best["likely_files"]
    assert best["suggested_next_action"].startswith("select for investigate")
    assert best["reject_reason"] == ""
    assert best["filtered_out"] is False

    claimed = by_number(result, 103)
    assert claimed["recommendation"] == "avoid"
    assert claimed["score"] < best["score"]
    assert "claimed or assigned" in claimed["penalties"]
    assert claimed["claimed_duplicate_stale_signal"] == "claimed"
    assert claimed["reject_reason"] == "already claimed or assigned"
    assert claimed["suggested_next_action"].startswith("skip:")

    large = by_number(result, 102)
    assert large["too_large"] is True
    assert large["score"] < best["score"]
    assert "large/refactor-like scope" in large["penalties"]
    assert large["scope_size_signal"] == "large"

    stale = by_number(result, 104)
    assert stale["score"] < best["score"]
    assert "stale without maintainer confirmation" in stale["penalties"]
    assert stale["stale"] is True
    assert stale["claimed_duplicate_stale_signal"] == "stale"

    auth = by_number(result, 105)
    assert auth["high_risk"] is True
    assert auth["score"] < best["score"]
    assert "high dependency/auth/core risk" in auth["penalties"]
    assert auth["maintainer_confirmed"] is True
    assert auth["risk_level"] == "high"
    assert "auth" in auth["subsystems"]

    duplicate = by_number(result, 106)
    assert duplicate["recommendation"] == "avoid"
    assert duplicate["duplicate"] is True
    assert duplicate["reject_reason"] == "duplicate candidate"
    assert "duplicate/already covered" in duplicate["penalties"]

    empty = run_scorer([])
    assert empty == {"status": "no_candidates", "candidates": []}

    print("candidate scoring regression passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
