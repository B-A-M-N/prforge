#!/usr/bin/env python3
"""Level 2 dry-run artifact verifier for PRForge.

Verifies that a completed Level 2 dry-run produced a maintainer-grade artifact trail:
  - All required artifacts present
  - Quality weakness gate passes (no BLOCKING_WEAKNESS)
  - Validation ledger contains real command output (not fabricated)
  - PR body has required sections
  - Contract scope is captured
  - git_state.json present and non-blocked
  - Approval preview text present and non-empty
  - No public action was taken (no push evidence in state)

Usage:
  python3 scripts/verify_level2_run.py <artifact_dir> [--json] [--repo <path>]

Exit codes:
  0 — All checks pass
  1 — One or more checks failed
  2 — Artifact dir not found or unreadable
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


REQUIRED_ARTIFACTS = [
    "state.json",
    "contract.md",
    "patch_plan.md",
    "dod.md",
    "repo_intelligence.md",
    "validation_ledger.md",
    "hostile_review.md",
    "pr_body.md",
    "approval.md",
]

PR_BODY_REQUIRED_SECTIONS = ["## Summary", "## Validation"]

CONTRACT_REQUIRED_FIELDS = ["objective", "allowed", "validation"]

VALIDATION_EVIDENCE_PATTERNS = [
    re.compile(r"Status:\s*(PASS|FAIL|passed|failed)", re.IGNORECASE),
    re.compile(r"Output:", re.IGNORECASE),
    re.compile(r"\$\s+\S"),
    re.compile(r"^\s*```", re.MULTILINE),
]

FABRICATION_PATTERNS = [
    re.compile(r"\[would run\]", re.IGNORECASE),
    re.compile(r"\[not run\]", re.IGNORECASE),
    re.compile(r"expected output", re.IGNORECASE),
    re.compile(r"hypothetically", re.IGNORECASE),
    re.compile(r"would produce", re.IGNORECASE),
]


def check(label: str, passed: bool, detail: str = "") -> dict:
    return {"label": label, "passed": passed, "detail": detail}


def verify(artifact_dir: Path, _repo: Path | None = None) -> list[dict]:
    results: list[dict] = []

    # ── Required artifacts present ───────────────────────────────────────────
    for name in REQUIRED_ARTIFACTS:
        path = artifact_dir / name
        if path.is_file() and path.stat().st_size > 0:
            results.append(check(f"artifact present: {name}", True))
        elif path.is_file():
            results.append(check(f"artifact present: {name}", False, "file exists but is empty"))
        else:
            results.append(check(f"artifact present: {name}", False, "file missing"))

    # ── state.json — phase must be PACKAGE or later, no push evidence ────────
    state_path = artifact_dir / "state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            phase = state.get("phase", "")
            valid_phases = {
                "PACKAGE", "APPROVAL", "POSTMORTEM", "MEMORY_INDEX", "COMPLETE",
            }
            if phase in valid_phases:
                results.append(check("state.phase is at PACKAGE or later", True, f"phase={phase}"))
            else:
                results.append(check(
                    "state.phase is at PACKAGE or later", False,
                    f"phase={phase!r} — run must have reached PACKAGE before verification",
                ))

            # No public action consumed
            approval = state.get("approval") or {}
            if approval.get("consumed") is True:
                results.append(check(
                    "no public action consumed", False,
                    "approval.consumed=true — push or PR creation already happened",
                ))
            else:
                results.append(check("no public action consumed", True))

        except Exception as exc:
            results.append(check("state.json readable", False, str(exc)))

    # ── Quality weakness gate ─────────────────────────────────────────────────
    gate_script = Path(__file__).parent / "quality_weakness_gate.py"
    if gate_script.is_file():
        proc = subprocess.run(
            [sys.executable, str(gate_script), str(artifact_dir), "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            results.append(check("quality weakness gate: no BLOCKING_WEAKNESS", True))
        else:
            try:
                gate_data = json.loads(proc.stdout or proc.stderr)
                worst = gate_data.get("worst", "UNKNOWN")
                count = gate_data.get("count", "?")
                findings = gate_data.get("findings", [])
                first = findings[0] if findings else {}
                detail = (
                    f"worst={worst}, count={count}"
                    + (f"; first: {first.get('artifact')}:{first.get('line')} '{first.get('phrase')}'" if first else "")
                )
            except Exception:
                detail = f"exit={proc.returncode}"
            results.append(check(
                "quality weakness gate: no BLOCKING_WEAKNESS", False, detail,
            ))
    else:
        results.append(check(
            "quality weakness gate: no BLOCKING_WEAKNESS", False,
            "quality_weakness_gate.py not found",
        ))

    # ── Validation ledger: real evidence, no fabrication ─────────────────────
    ledger_path = artifact_dir / "validation_ledger.md"
    if ledger_path.is_file():
        ledger_text = ledger_path.read_text(encoding="utf-8")
        has_evidence = any(p.search(ledger_text) for p in VALIDATION_EVIDENCE_PATTERNS)
        results.append(check(
            "validation ledger has real command evidence", has_evidence,
            "" if has_evidence else "no Status/Output/shell-command patterns found",
        ))
        fabricated = [p.pattern for p in FABRICATION_PATTERNS if p.search(ledger_text)]
        results.append(check(
            "validation ledger: no fabrication markers", not fabricated,
            f"found: {fabricated}" if fabricated else "",
        ))

    # ── PR body: required sections ────────────────────────────────────────────
    pr_body_path = artifact_dir / "pr_body.md"
    if pr_body_path.is_file():
        pr_text = pr_body_path.read_text(encoding="utf-8")
        for section in PR_BODY_REQUIRED_SECTIONS:
            has = section.lower() in pr_text.lower()
            results.append(check(
                f"pr_body.md has section: {section}", has,
                "" if has else f"{section} not found in pr_body.md",
            ))

    # ── Contract: scope captured ──────────────────────────────────────────────
    contract_path = artifact_dir / "contract.md"
    if contract_path.is_file():
        contract_text = contract_path.read_text(encoding="utf-8").lower()
        for field in CONTRACT_REQUIRED_FIELDS:
            has = field in contract_text
            results.append(check(
                f"contract.md captures: {field}", has,
                "" if has else f"'{field}' keyword not found in contract.md",
            ))

    # ── git_state.json present and non-blocked ────────────────────────────────
    git_state_path = artifact_dir / "git_state.json"
    if not git_state_path.is_file():
        results.append(check(
            "git_state.json present and non-blocked", False,
            "git_state.json missing — run git_state_check.py before packaging",
        ))
    else:
        try:
            git_state = json.loads(git_state_path.read_text(encoding="utf-8"))
            rec = git_state.get("recommended_state", "UNKNOWN")
            if rec in ("BLOCKED", "REBASE_REQUIRED"):
                blocking = git_state.get("blocking_reasons", [])
                detail = "; ".join(blocking[:2]) if blocking else rec
                results.append(check("git_state.json present and non-blocked", False, detail))
            else:
                results.append(check("git_state.json present and non-blocked", True, f"state={rec}"))
        except Exception as exc:
            results.append(check("git_state.json present and non-blocked", False, str(exc)))

    # ── Approval preview text present ─────────────────────────────────────────
    approval_path = artifact_dir / "approval.md"
    if approval_path.is_file():
        approval_text = approval_path.read_text(encoding="utf-8").strip()
        has_preview = len(approval_text) > 100
        # Look for approved command or public text preview
        has_command = bool(
            re.search(r"(Approved command|git push|gh pr create|approved_actions)", approval_text, re.IGNORECASE)
        )
        results.append(check(
            "approval.md has public text preview", has_preview and has_command,
            "" if (has_preview and has_command) else
            ("approval.md too short" if not has_preview else "no approved command found in approval.md"),
        ))

    # ── DoD completeness ──────────────────────────────────────────────────────
    dod_path = artifact_dir / "dod.md"
    if dod_path.is_file():
        dod_text = dod_path.read_text(encoding="utf-8")
        unchecked = re.findall(r"^- \[ \]", dod_text, re.MULTILINE)
        checked = re.findall(r"^- \[x\]", dod_text, re.IGNORECASE | re.MULTILINE)
        if unchecked:
            results.append(check(
                "dod.md: all items checked or justified", False,
                f"{len(unchecked)} unchecked item(s), {len(checked)} checked",
            ))
        elif checked:
            results.append(check(
                "dod.md: all items checked or justified", True,
                f"{len(checked)} item(s) checked",
            ))
        else:
            results.append(check("dod.md: all items checked or justified", False, "no checklist items found"))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Level 2 dry-run artifact verifier")
    parser.add_argument("artifact_dir", help="Path to PRForge artifact directory")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--repo", default="", help="Path to target repo (optional)")
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir).resolve()
    if not artifact_dir.is_dir():
        print(f"ERROR: artifact directory not found: {artifact_dir}", file=sys.stderr)
        return 2

    repo = Path(args.repo).resolve() if args.repo else None

    results = verify(artifact_dir, _repo=repo)

    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])
    total = len(results)

    if args.json:
        print(json.dumps({
            "artifact_dir": str(artifact_dir),
            "passed": passed,
            "failed": failed,
            "total": total,
            "certified": failed == 0,
            "checks": results,
        }, indent=2))
    else:
        print(f"Level 2 artifact verification: {artifact_dir}")
        print()
        for r in results:
            symbol = "PASS" if r["passed"] else "FAIL"
            line = f"  {symbol}  {r['label']}"
            if r.get("detail"):
                line += f" — {r['detail']}"
            print(line)
        print()
        print(f"  Result: {'CERTIFIED' if failed == 0 else 'PARTIAL'} ({passed}/{total} checks passed)")
        if failed > 0:
            print(f"  {failed} check(s) failed — resolve before claiming Level 2 certified")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
