#!/usr/bin/env python3
"""Tests for the PRForge quality weakness gate.

All fixtures use neutral, domain-agnostic language — not the OpenDeepWikiX
cross-repo search domain. The gate must catch pattern classes generically.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATE = ROOT / "scripts" / "quality_weakness_gate.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_gate(artifact_files: dict[str, str], extra_args: list[str] | None = None) -> tuple[int, dict]:
    """Write artifact files to a temp dir and run the gate. Returns (exit_code, json_output)."""
    with tempfile.TemporaryDirectory(prefix="prforge-qw-test.") as td:
        tdp = Path(td)
        for name, content in artifact_files.items():
            (tdp / name).write_text(content, encoding="utf-8")
        args = [sys.executable, str(GATE), str(tdp), "--json"] + (extra_args or [])
        result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=10)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            data = {"raw_stdout": result.stdout, "raw_stderr": result.stderr}
        return result.returncode, data


# ---------------------------------------------------------------------------
# Test 1: BLOCKING_WEAKNESS — "LLM synthesizes" on core objective
# ---------------------------------------------------------------------------

def test_llm_synthesize_blocks() -> None:
    artifacts = {
        "self_review.md": (
            "## Verdict: APPROVE\n\n"
            "The LLM can still synthesize a response from empty results.\n"
            "Core search path returns no hits.\n"
        )
    }
    rc, data = run_gate(artifacts)
    assert rc == 2, f"expected exit 2 (BLOCKING_WEAKNESS), got {rc}. data={data}"
    findings = data.get("findings", [])
    blocking = [f for f in findings if f["severity"] == "BLOCKING_WEAKNESS"]
    assert blocking, "expected BLOCKING_WEAKNESS findings"
    assert data["worst"] == "BLOCKING_WEAKNESS"
    print("  test_llm_synthesize_blocks: PASS")


# ---------------------------------------------------------------------------
# Test 2: BLOCKING_WEAKNESS — PR body claims feature complete with empty evidence
# ---------------------------------------------------------------------------

def test_pr_body_empty_fallback_blocks() -> None:
    artifacts = {
        "pr_body.md": (
            "## Summary\n"
            "Feature is complete and working.\n\n"
            "## Validation\n"
            "The fallback is empty but gracefully handles empty results in all cases.\n"
        )
    }
    rc, data = run_gate(artifacts)
    assert rc == 2, f"expected exit 2 (BLOCKING_WEAKNESS), got {rc}"
    findings = data.get("findings", [])
    blocking = [f for f in findings if f["severity"] == "BLOCKING_WEAKNESS"]
    assert blocking, "expected BLOCKING_WEAKNESS for 'gracefully handles empty'"
    print("  test_pr_body_empty_fallback_blocks: PASS")


# ---------------------------------------------------------------------------
# Test 3: BLOCKING_WEAKNESS — validation ledger has "no tests for" a changed file
# ---------------------------------------------------------------------------

def test_no_tests_for_changed_file_blocks() -> None:
    artifacts = {
        "validation_ledger.md": (
            "| File | Test Status |\n"
            "|------|-------------|\n"
            "| src/auth/token.py | no tests for this module |\n"
            "| src/cache/store.py | passing |\n"
        )
    }
    rc, data = run_gate(artifacts)
    assert rc == 2, f"expected exit 2, got {rc}"
    assert data["worst"] == "BLOCKING_WEAKNESS"
    print("  test_no_tests_for_changed_file_blocks: PASS")


# ---------------------------------------------------------------------------
# Test 4: ACCEPTABLE_LIMITATION — documented non-core limitation with tracking issue
# ---------------------------------------------------------------------------

def test_tracked_limitation_is_acceptable() -> None:
    artifacts = {
        "approval.md": (
            "## Known Limitations\n\n"
            "PDF export for large files is out of scope for this PR.\n"
            "Tracked in issue #42.\n\n"
            "## Status: READY_WITH_WARNINGS\n"
        )
    }
    rc, data = run_gate(artifacts)
    # Should not be BLOCKING (exit 2)
    assert rc != 2, f"expected non-blocking exit, got {rc}"
    findings = data.get("findings", [])
    acceptable = [f for f in findings if f["severity"] == "ACCEPTABLE_LIMITATION"]
    assert acceptable, "expected ACCEPTABLE_LIMITATION for tracked out-of-scope item"
    blocking = [f for f in findings if f["severity"] == "BLOCKING_WEAKNESS"]
    assert not blocking, f"unexpected blocking findings: {blocking}"
    print("  test_tracked_limitation_is_acceptable: PASS")


# ---------------------------------------------------------------------------
# Test 5: REQUIRES_APPROVAL — "accepted for v1" must be in approval.md, not self-approved
# ---------------------------------------------------------------------------

def test_accepted_for_v1_requires_approval() -> None:
    artifacts = {
        "self_review.md": (
            "## Search Quality\n"
            "The full-text indexing is accepted for v1 even though recall is low.\n"
            "## Verdict: APPROVE\n"
        )
    }
    rc, data = run_gate(artifacts)
    # Should be exit 1 (REQUIRES_APPROVAL) or exit 2 (if "low recall" also triggers)
    assert rc in (1, 2), f"expected exit 1 or 2, got {rc}"
    findings = data.get("findings", [])
    ra_or_block = [f for f in findings if f["severity"] in ("REQUIRES_APPROVAL", "BLOCKING_WEAKNESS")]
    assert ra_or_block, "expected at least REQUIRES_APPROVAL finding"
    print("  test_accepted_for_v1_requires_approval: PASS")


# ---------------------------------------------------------------------------
# Test 6: BLOCKED when core objective has untested known weakness
# ---------------------------------------------------------------------------

def test_core_weakness_no_test_coverage_blocks() -> None:
    artifacts = {
        "hostile_review.md": (
            "## Verdict: PASS\n\n"
            "Correctness: All paths tested.\n"
            "Exception: manual only validation for the ingestion pipeline.\n"
            "No tests for the retry logic.\n"
        )
    }
    rc, data = run_gate(artifacts)
    assert rc == 2, f"expected BLOCKED, got {rc}"
    print("  test_core_weakness_no_test_coverage_blocks: PASS")


# ---------------------------------------------------------------------------
# Test 7: Gate reports exact artifact, line, phrase, severity, repair action
# ---------------------------------------------------------------------------

def test_report_includes_location_and_repair() -> None:
    artifacts = {
        "plan.md": (
            "## Approach\n"
            "The cache invalidation logic is probably correct under concurrent writes.\n"
        )
    }
    rc, data = run_gate(artifacts)
    assert rc in (1, 2), f"expected non-zero exit, got {rc}"
    findings = data.get("findings", [])
    assert findings, "expected findings"
    f = findings[0]
    assert f["artifact"] == "plan.md", f"unexpected artifact: {f['artifact']}"
    assert f["line"] > 0
    assert f["phrase"], "phrase must be non-empty"
    assert f["severity"] in ("BLOCKING_WEAKNESS", "REQUIRES_APPROVAL", "ACCEPTABLE_LIMITATION")
    assert f["reason"], "reason must be non-empty"
    assert f["repair"], "repair action must be non-empty"
    print("  test_report_includes_location_and_repair: PASS")


# ---------------------------------------------------------------------------
# Test 8: Clean artifact set — exit 0
# ---------------------------------------------------------------------------

def test_clean_artifacts_pass() -> None:
    artifacts = {
        "self_review.md": (
            "## Verdict: PASS\n\n"
            "All changed files have corresponding test coverage.\n"
            "Build: 0 errors. Linter: clean.\n"
            "Correctness: integration test confirms behavior under concurrent writes.\n"
        ),
        "pr_body.md": (
            "## Summary\n"
            "- Adds retry logic to the cache invalidation path\n"
            "- All paths covered by deterministic unit tests\n\n"
            "## Test Plan\n"
            "- [x] `pytest tests/cache/` — passed\n"
            "- [x] `mypy src/cache/` — clean\n"
        ),
    }
    rc, data = run_gate(artifacts)
    assert rc == 0, f"expected clean exit 0, got {rc}. findings={data.get('findings')}"
    assert data.get("worst") is None
    assert data.get("count") == 0
    print("  test_clean_artifacts_pass: PASS")


# ---------------------------------------------------------------------------
# Test 9: --max-severity flag — REQUIRES_APPROVAL doesn't block at BLOCKING_WEAKNESS threshold
# ---------------------------------------------------------------------------

def test_max_severity_flag() -> None:
    artifacts = {
        "approval.md": (
            "## Known Tradeoffs\n"
            "The lazy evaluation path is a known tradeoff for startup performance.\n"
        )
    }
    # Default threshold is REQUIRES_APPROVAL → REQUIRES_APPROVAL exits 1 (not 2)
    rc, _d0 = run_gate(artifacts)
    assert rc == 1, f"expected exit 1 (REQUIRES_APPROVAL default threshold), got {rc}"

    # With --max-severity=REQUIRES_APPROVAL, same result
    rc2, _d2 = run_gate(artifacts, extra_args=["--max-severity=REQUIRES_APPROVAL"])
    assert rc2 == 1, f"expected exit 1 with --max-severity=REQUIRES_APPROVAL, got {rc2}"

    # With --max-severity=ACCEPTABLE_LIMITATION, REQUIRES_APPROVAL still triggers exit 1
    rc3, _d3 = run_gate(artifacts, extra_args=["--max-severity=ACCEPTABLE_LIMITATION"])
    assert rc3 == 1, f"expected exit 1 with --max-severity=ACCEPTABLE_LIMITATION, got {rc3}"

    # With --max-severity=BLOCKING_WEAKNESS, REQUIRES_APPROVAL-only findings exit 0
    rc4, _d4 = run_gate(artifacts, extra_args=["--max-severity=BLOCKING_WEAKNESS"])
    assert rc4 == 0, f"expected exit 0 with --max-severity=BLOCKING_WEAKNESS for RA-only finding, got {rc4}"
    print("  test_max_severity_flag: PASS")


# ---------------------------------------------------------------------------
# Test 10: Multiple artifacts scanned — findings from all files
# ---------------------------------------------------------------------------

def test_multiple_artifact_files_scanned() -> None:
    artifacts = {
        "self_review.md": "## Verdict: PASS\nAll good.\n",
        "pr_body.md": "## Summary\nFeature is complete.\nTODO: add metrics endpoint.\n",
        "validation_ledger.md": "All commands passed.\n",
    }
    rc, data = run_gate(artifacts)
    # "TODO" triggers REQUIRES_APPROVAL
    assert rc in (1, 2), f"expected non-zero for TODO, got {rc}"
    findings = data.get("findings", [])
    pr_body_findings = [f for f in findings if f["artifact"] == "pr_body.md"]
    assert pr_body_findings, "expected finding in pr_body.md"
    print("  test_multiple_artifact_files_scanned: PASS")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    tests = [
        test_llm_synthesize_blocks,
        test_pr_body_empty_fallback_blocks,
        test_no_tests_for_changed_file_blocks,
        test_tracked_limitation_is_acceptable,
        test_accepted_for_v1_requires_approval,
        test_core_weakness_no_test_coverage_blocks,
        test_report_includes_location_and_repair,
        test_clean_artifacts_pass,
        test_max_severity_flag,
        test_multiple_artifact_files_scanned,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as exc:
            print(f"  FAIL {t.__name__}: {exc}")
            failed += 1

    print(f"\nquality-weakness-gate tests: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
