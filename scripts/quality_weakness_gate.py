#!/usr/bin/env python3
"""PRForge quality weakness gate.

Scans self-review, validation, package, and approval artifacts for
weakness-acceptance language and classifies each match as:
  ACCEPTABLE_LIMITATION  — non-core, documented, has fallback, allowed with note
  REQUIRES_APPROVAL      — real tradeoff, must appear explicitly in approval.md
  BLOCKING_WEAKNESS      — affects core objective; blocks APPROVE verdict

Exit codes:
  0  — clean (or only ACCEPTABLE_LIMITATION findings)
  1  — REQUIRES_APPROVAL findings present (warning, caller decides)
  2  — BLOCKING_WEAKNESS found (hard block)
  3  — usage / I/O error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Pattern catalog
# ---------------------------------------------------------------------------

Severity = Literal["BLOCKING_WEAKNESS", "REQUIRES_APPROVAL", "ACCEPTABLE_LIMITATION"]

@dataclass(frozen=True)
class WeaknessPattern:
    regex: str
    severity: Severity
    reason: str
    repair: str

# Each tuple: (regex, severity, why-it-matters, what-to-do)
_RAW_PATTERNS: list[tuple[str, Severity, str, str]] = [
    # ── BLOCKING_WEAKNESS ───────────────────────────────────────────────────
    (
        r"(?i)\b(llm|model|ai)\s+(can\s+)?(still\s+)?synthesi[sz]e",
        "BLOCKING_WEAKNESS",
        "Core evidence path is empty; the model is being asked to compensate for missing data.",
        "Add real evidence (search results, test output, log samples). Remove the claim or provide the evidence.",
    ),
    (
        r"(?i)(fallback\s+is\s+empty|gracefully\s+handles?\s+empty|empty\s+result[s]?\s+(is|are)\s+(ok|fine|acceptable))",
        "BLOCKING_WEAKNESS",
        "Empty evidence accepted as OK on a code path that is supposed to produce results.",
        "Provide non-empty evidence, or classify the case as explicitly out-of-scope with a test.",
    ),
    (
        r"(?i)\bno\s+tests?\s+(for|cover(ing)?)\b",
        "BLOCKING_WEAKNESS",
        "Core changed code has no test coverage stated in the artifact.",
        "Add tests before advancing. If tests are impossible, document why with an explicit user decision.",
    ),
    (
        r"(?i)\bmanual\s+only\b",
        "BLOCKING_WEAKNESS",
        "Validation is manual-only, meaning it cannot be reproduced deterministically.",
        "Add an automated check. If unavailable, escalate to REQUIRES_APPROVAL with justification.",
    ),
    (
        r"(?i)\b(not\s+implemented|placeholder)\b",
        "BLOCKING_WEAKNESS",
        "A required piece of the implementation is absent.",
        "Implement the missing piece or descope it explicitly before packaging.",
    ),
    (
        r"(?i)\b(low\s+recall|low[-\s]confidence\s+evidence|weak\s+evidence)\b",
        "BLOCKING_WEAKNESS",
        "Core search or evidence mechanism is acknowledged as low-quality.",
        "Fix the evidence path. If truly out of scope, document and get explicit approval.",
    ),
    (
        r"(?i)\bhides?\s+(missing\s+)?(validation|evidence|results?)\b",
        "BLOCKING_WEAKNESS",
        "Artifact explicitly notes that missing validation is being hidden.",
        "Surface the missing validation and resolve it.",
    ),
    (
        r"(?i)\bunsupported\s+claim\b",
        "BLOCKING_WEAKNESS",
        "A claim in the artifact has no supporting evidence.",
        "Remove the claim or provide evidence.",
    ),

    # ── REQUIRES_APPROVAL ───────────────────────────────────────────────────
    (
        r"(?i)\b(known\s+tradeoff|known\s+limitation)\b",
        "REQUIRES_APPROVAL",
        "A deliberate tradeoff is acknowledged. Must appear in approval.md for explicit sign-off.",
        "Add a 'Known Tradeoffs' section to approval.md with the tradeoff and the explicit user decision.",
    ),
    (
        r"(?i)\baccepted\s+for\s+v\d+\b",
        "REQUIRES_APPROVAL",
        "A weakness is deferred to a future version without explicit maintainer sign-off.",
        "Escalate to approval.md as a needs_user_decision item. Cannot self-approve.",
    ),
    (
        r"(?i)\b(best\s+effort|good\s+enough)\b",
        "REQUIRES_APPROVAL",
        "Effort qualifier signals the implementation is below the normal quality bar.",
        "Define the actual quality bar and either meet it or get explicit approval to ship below it.",
    ),
    (
        r"(?i)\b(not\s+ideal|less\s+than\s+ideal|suboptimal)\b",
        "REQUIRES_APPROVAL",
        "Implementation is acknowledged as below ideal. Requires explicit maintainer decision.",
        "State what the ideal solution is and why it was not done. Escalate to approval.md.",
    ),
    (
        r"(?i)\b(future\s+improvement|follow[-\s]up|todo)\b",
        "REQUIRES_APPROVAL",
        "Deferred work without an explicit owner or tracking item.",
        "Create a tracking issue before shipping. Include the issue number in approval.md.",
    ),
    (
        r"(?i)\b(probably|maybe|might\s+work|should\s+work)\b",
        "REQUIRES_APPROVAL",
        "Uncertain language about correctness — not appropriate in a ship artifact.",
        "Replace with confirmed behavior based on actual test output. Remove hedging.",
    ),
    (
        r"(?i)\b(degraded\s+but\s+acceptable|works?\s+enough)\b",
        "REQUIRES_APPROVAL",
        "Degraded behavior accepted without an explicit decision.",
        "Define 'acceptable' with a threshold. Get explicit approval or fix the degradation.",
    ),
    (
        r"(?i)\bneeds?\s+user\s+decision\b",
        "REQUIRES_APPROVAL",
        "Item marked as needing user decision but approval.md may not have it.",
        "Ensure the item appears in approval.md with the user's explicit response.",
    ),

    # ── ACCEPTABLE_LIMITATION (logged, not blocking) ─────────────────────
    (
        r"(?i)\b(out\s+of\s+scope|explicitly\s+(out\s+of|de[-]?scoped))\b",
        "ACCEPTABLE_LIMITATION",
        "Item is explicitly descoped from this PR.",
        "Verify the descoping is documented and a tracking issue exists.",
    ),
    (
        r"(?i)\b(tracked\s+in\s+(issue|ticket)\s+#?\d+)\b",
        "ACCEPTABLE_LIMITATION",
        "Known limitation with a tracking reference.",
        "Verify the issue number is real and included in approval.md.",
    ),
]

PATTERNS: list[WeaknessPattern] = [
    WeaknessPattern(regex=r, severity=s, reason=rs, repair=rp)
    for r, s, rs, rp in _RAW_PATTERNS
]

# ---------------------------------------------------------------------------
# Artifact scanner
# ---------------------------------------------------------------------------

ARTIFACT_FILES = [
    "self_review.md",
    "hostile_review.md",
    "plan.md",
    "pr_package.md",
    "pr_body.md",
    "approval.md",
    "validation_ledger.md",
    "dod.md",
]


@dataclass
class Finding:
    artifact: str
    line_number: int
    phrase: str
    severity: Severity
    reason: str
    repair: str
    pattern: str


def scan_artifact(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return findings

    for lineno, line in enumerate(text.splitlines(), start=1):
        for pat in PATTERNS:
            m = re.search(pat.regex, line)
            if m:
                findings.append(Finding(
                    artifact=path.name,
                    line_number=lineno,
                    phrase=m.group(0),
                    severity=pat.severity,
                    reason=pat.reason,
                    repair=pat.repair,
                    pattern=pat.regex,
                ))
                break  # one finding per line
    return findings


def scan_artifact_dir(artifact_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    for name in ARTIFACT_FILES:
        p = artifact_dir / name
        if p.exists():
            findings.extend(scan_artifact(p))
    return findings


# ---------------------------------------------------------------------------
# Classification summary
# ---------------------------------------------------------------------------

def worst_severity(findings: list[Finding]) -> Severity | None:
    order: dict[Severity, int] = {
        "BLOCKING_WEAKNESS": 3,
        "REQUIRES_APPROVAL": 2,
        "ACCEPTABLE_LIMITATION": 1,
    }
    if not findings:
        return None
    return max(findings, key=lambda f: order[f.severity]).severity


def report(findings: list[Finding], *, json_out: bool = False) -> None:
    if json_out:
        print(json.dumps(
            {
                "findings": [
                    {
                        "artifact": f.artifact,
                        "line": f.line_number,
                        "phrase": f.phrase,
                        "severity": f.severity,
                        "reason": f.reason,
                        "repair": f.repair,
                    }
                    for f in findings
                ],
                "worst": worst_severity(findings),
                "count": len(findings),
            },
            indent=2,
        ))
        return

    if not findings:
        print("quality-weakness-gate: OK — no weakness patterns found")
        return

    by_severity: dict[str, list[Finding]] = {
        "BLOCKING_WEAKNESS": [],
        "REQUIRES_APPROVAL": [],
        "ACCEPTABLE_LIMITATION": [],
    }
    for f in findings:
        by_severity[f.severity].append(f)

    for sev in ("BLOCKING_WEAKNESS", "REQUIRES_APPROVAL", "ACCEPTABLE_LIMITATION"):
        grp = by_severity[sev]
        if not grp:
            continue
        label = {"BLOCKING_WEAKNESS": "BLOCKED", "REQUIRES_APPROVAL": "REQUIRES_APPROVAL", "ACCEPTABLE_LIMITATION": "WARNING"}[sev]
        print(f"\n[{label}] {len(grp)} finding(s):")
        for f in grp:
            print(f"  {f.artifact}:{f.line_number}  phrase={repr(f.phrase)}")
            print(f"    why:    {f.reason}")
            print(f"    repair: {f.repair}")

    ws = worst_severity(findings)
    assert ws is not None  # findings is non-empty here
    summary: dict[Severity, str] = {
        "BLOCKING_WEAKNESS": "BLOCKED — cannot approve until BLOCKING_WEAKNESS findings are resolved.",
        "REQUIRES_APPROVAL": "REQUIRES_APPROVAL — add these items to approval.md for explicit sign-off.",
        "ACCEPTABLE_LIMITATION": "WARNING — limitations noted; verify tracking issues exist.",
    }
    print(f"\nquality-weakness-gate: {summary[ws]}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan PRForge artifacts for weakness-acceptance patterns."
    )
    parser.add_argument("artifact_dir", help="Path to the run artifact directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--max-severity",
        choices=["BLOCKING_WEAKNESS", "REQUIRES_APPROVAL", "ACCEPTABLE_LIMITATION"],
        default="REQUIRES_APPROVAL",
        help=(
            "Exit non-zero only if worst finding is at or above this level "
            "(default: REQUIRES_APPROVAL — exits 2 for BLOCKING_WEAKNESS, 1 for REQUIRES_APPROVAL). "
            "Use BLOCKING_WEAKNESS to only hard-block, ignoring REQUIRES_APPROVAL."
        ),
    )
    args = parser.parse_args(argv)

    artifact_dir = Path(args.artifact_dir)
    if not artifact_dir.is_dir():
        print(f"error: artifact_dir not found: {artifact_dir}", file=sys.stderr)
        return 3

    findings = scan_artifact_dir(artifact_dir)
    report(findings, json_out=args.json)

    ws = worst_severity(findings)
    if ws is None:
        return 0

    order: dict[str, int] = {
        "BLOCKING_WEAKNESS": 3,
        "REQUIRES_APPROVAL": 2,
        "ACCEPTABLE_LIMITATION": 1,
    }
    threshold = order[args.max_severity]
    severity_order = order[ws]

    if ws == "BLOCKING_WEAKNESS" and severity_order >= threshold:
        return 2
    if ws == "REQUIRES_APPROVAL" and severity_order >= threshold:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
