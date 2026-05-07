#!/usr/bin/env python3
"""deterministic candidate discovery scoring for prforge."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


BLOCKING_LABELS = {
    "blocked",
    "wontfix",
    "won't fix",
    "needs decision",
    "needs-design",
    "needs design",
}
HIGH_RISK_LABELS = {"auth", "oauth", "security", "dependencies", "dependency", "breaking-change"}
SMALL_LABELS = {"good first issue", "help wanted", "bug", "documentation", "docs", "test"}
LARGE_LABELS = {"refactor", "rewrite", "architecture", "epic", "large", "tracking"}
DUPLICATE_LABELS = {"duplicate"}
CLAIM_PATTERNS = [
    r"\bi'?ll work on this\b",
    r"\bi am working on this\b",
    r"\bworking on this\b",
    r"\bassign(?:ed)? me\b",
    r"\btaken\b",
]
MAINTAINER_CONFIRM_PATTERNS = [
    r"\bconfirmed\b",
    r"\bvalid\b",
    r"\bagreed\b",
    r"\baccepted\b",
    r"\bsounds good\b",
    r"\bplease send (?:a )?pr\b",
]
TESTABILITY_PATTERNS = [
    r"\btest\b",
    r"\brepro\b",
    r"\breproduction\b",
    r"\bexpected\b",
    r"\bactual\b",
    r"\bfailing\b",
]

FILE_HINTS = [
    (("parser", "parse", "payload"), ("parser", ["src/parser/", "tests/"])),
    (("auth", "oauth", "token", "credential", "permission"), ("auth", ["src/auth/", "tests/"])),
    (("dependency", "dependencies", "package", "lockfile"), ("dependencies", ["package.json", "requirements.txt", "pyproject.toml"])),
    (("docs", "documentation", "readme"), ("docs", ["docs/", "README.md"])),
    (("config", "warning", "settings"), ("config", ["config/", "tests/"])),
    (("ci", "workflow", "action"), ("ci", [".github/workflows/"])),
    (("test", "coverage", "failing"), ("tests", ["tests/"])),
]


def _labels(candidate: dict[str, Any]) -> set[str]:
    raw = candidate.get("labels") or []
    labels: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            labels.add(item.lower())
        elif isinstance(item, dict) and item.get("name"):
            labels.add(str(item["name"]).lower())
    return labels


def _comments(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    raw = candidate.get("comments") or []
    return [item for item in raw if isinstance(item, dict)]


def _text(candidate: dict[str, Any]) -> str:
    parts = [str(candidate.get("title") or ""), str(candidate.get("body") or "")]
    parts.extend(str(c.get("body") or "") for c in _comments(candidate))
    return "\n".join(parts).lower()


def _has_pattern(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _maintainer_comments(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    maintainers = set(candidate.get("maintainers") or [])
    maintainers = {str(m).lower() for m in maintainers}
    comments = _comments(candidate)
    if maintainers:
        return [
            c for c in comments
            if str((c.get("author") or {}).get("login") or c.get("author") or "").lower() in maintainers
        ]
    return [c for c in comments if c.get("author_association") in {"MEMBER", "OWNER", "COLLABORATOR"}]


def classify_candidate(candidate: dict[str, Any]) -> str:
    labels = _labels(candidate)
    text = _text(candidate)
    if "documentation" in labels or "docs" in labels or "readme" in text:
        return "docs"
    if "test" in labels or "coverage" in text:
        return "test"
    if "bug" in labels or re.search(r"\b(fix|broken|regression|error|crash)\b", text):
        return "bug"
    if "enhancement" in labels or "feature" in labels or re.search(r"\b(add|support|implement)\b", text):
        return "feature"
    if "refactor" in labels or "cleanup" in text:
        return "refactor"
    return "other"


def infer_subsystems_and_files(candidate: dict[str, Any]) -> tuple[list[str], list[str]]:
    explicit_files = [str(item) for item in candidate.get("likely_files") or candidate.get("files") or [] if item]
    explicit_subsystems = [str(item) for item in candidate.get("subsystems") or [] if item]
    if explicit_files or explicit_subsystems:
        return _dedupe(explicit_subsystems), _dedupe(explicit_files)

    text = _text(candidate)
    subsystems: list[str] = []
    files: list[str] = []
    for keywords, (subsystem, hints) in FILE_HINTS:
        if any(keyword in text for keyword in keywords):
            subsystems.append(subsystem)
            files.extend(hints)

    return _dedupe(subsystems), _dedupe(files)


def summarize_signals(
    *,
    claimed: bool,
    duplicate: bool,
    stale: bool,
    testable: bool,
    maintainer_confirmed: bool,
    large: bool,
    high_risk: bool,
    blocked: bool,
    reasons: list[str],
    penalties: list[str],
    recommendation: str,
) -> dict[str, Any]:
    if high_risk or blocked:
        risk_level = "high"
    elif large or stale or claimed or duplicate:
        risk_level = "medium"
    else:
        risk_level = "low"

    if testable:
        testability_signal = "strong" if "locally testable" in reasons else "medium"
    else:
        testability_signal = "weak"

    maintainer_signal = "confirmed" if maintainer_confirmed else "unconfirmed"
    scope_size_signal = "large" if large else "small"
    if high_risk and not large:
        scope_size_signal = "medium"

    claimed_parts: list[str] = []
    if claimed:
        claimed_parts.append("claimed")
    if duplicate:
        claimed_parts.append("duplicate")
    if stale:
        claimed_parts.append("stale")
    claimed_signal = ", ".join(claimed_parts) if claimed_parts else "clear"

    reject_reason = ""
    if blocked:
        reject_reason = "blocked by repository label"
    elif duplicate:
        reject_reason = "duplicate candidate"
    elif claimed:
        reject_reason = "already claimed or assigned"

    if reject_reason:
        suggested_next_action = f"skip: {reject_reason}"
    elif recommendation == "best":
        suggested_next_action = "select for investigate; confirm repo contract and exact validation commands"
    elif recommendation == "ok":
        suggested_next_action = "investigate before selecting; verify local test path and maintainer intent"
    else:
        suggested_next_action = "defer unless maintainer confirms scope and validation path"

    reason_summary = "; ".join(reasons + penalties) or "no strong signals"
    return {
        "risk_level": risk_level,
        "reason_summary": reason_summary,
        "testability_signal": testability_signal,
        "maintainer_signal": maintainer_signal,
        "scope_size_signal": scope_size_signal,
        "claimed_duplicate_stale_signal": claimed_signal,
        "suggested_next_action": suggested_next_action,
        "reject_reason": reject_reason,
        "filtered_out": bool(reject_reason),
    }


def score_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    labels = _labels(candidate)
    text = _text(candidate)
    reasons: list[str] = []
    penalties: list[str] = []
    score = 50

    assignees = candidate.get("assignees") or []
    claimed = bool(assignees) or _has_pattern(text, CLAIM_PATTERNS)
    blocked = bool(labels & BLOCKING_LABELS)
    duplicate = bool(labels & DUPLICATE_LABELS) or bool(re.search(r"\bduplicate\b|\bdupe\b|\balready covered\b|\balready fixed\b", text))
    high_risk = bool(labels & HIGH_RISK_LABELS) or bool(re.search(r"\b(auth|oauth|token|credential|permission|dependency|core api)\b", text))
    large = bool(labels & LARGE_LABELS) or bool(re.search(r"\b(rewrite|architecture|large refactor|many files|whole system)\b", text))
    stale = bool(candidate.get("stale")) or int(candidate.get("age_days") or 0) > 730

    maintainer_text = "\n".join(str(c.get("body") or "") for c in _maintainer_comments(candidate)).lower()
    maintainer_confirmed = _has_pattern(maintainer_text, MAINTAINER_CONFIRM_PATTERNS)
    testable = bool(candidate.get("tests_available")) or _has_pattern(text, TESTABILITY_PATTERNS)
    reproducible = bool(candidate.get("reproducible")) or bool(re.search(r"\bsteps to reproduce\b|\brepro\b", text))

    if labels & SMALL_LABELS:
        score += 12
        reasons.append("small/help-wanted signal")
    if testable:
        score += 18
        reasons.append("locally testable")
    if reproducible:
        score += 10
        reasons.append("clear repro")
    if maintainer_confirmed:
        score += 18
        reasons.append("maintainer confirmed")
    if candidate.get("recent_merged_prs"):
        score += 7
        reasons.append("repo appears responsive")

    if claimed:
        score -= 45
        penalties.append("claimed or assigned")
    if duplicate:
        score -= 55
        penalties.append("duplicate/already covered")
    if blocked:
        score -= 60
        penalties.append("blocked/needs decision label")
    if large:
        score -= 30
        penalties.append("large/refactor-like scope")
    if high_risk:
        score -= 25
        penalties.append("high dependency/auth/core risk")
    if stale and not maintainer_confirmed:
        score -= 22
        penalties.append("stale without maintainer confirmation")
    if not testable:
        score -= 10
        penalties.append("weak local testability")

    score = max(0, min(100, score))
    if blocked or claimed or duplicate:
        recommendation = "avoid"
    elif score >= 75:
        recommendation = "best"
    elif score >= 50:
        recommendation = "ok"
    else:
        recommendation = "risky"

    subsystems, likely_files = infer_subsystems_and_files(candidate)
    signals = summarize_signals(
        claimed=claimed,
        duplicate=duplicate,
        stale=stale,
        testable=testable,
        maintainer_confirmed=maintainer_confirmed,
        large=large,
        high_risk=high_risk,
        blocked=blocked,
        reasons=reasons,
        penalties=penalties,
        recommendation=recommendation,
    )

    return {
        "number": candidate.get("number"),
        "title": candidate.get("title", ""),
        "url": candidate.get("url", ""),
        "type": classify_candidate(candidate),
        "score": score,
        "recommendation": recommendation,
        "claimed": claimed,
        "high_risk": high_risk,
        "too_large": large,
        "duplicate": duplicate,
        "stale": stale,
        "maintainer_confirmed": maintainer_confirmed,
        "testable": testable,
        "likely_files": likely_files,
        "subsystems": subsystems,
        "reasons": reasons,
        "penalties": penalties,
        **signals,
    }


def rank_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {"status": "no_candidates", "candidates": []}
    scored = [score_candidate(candidate) for candidate in candidates]
    scored.sort(key=lambda item: (-item["score"], item["number"] or 0, item["title"]))
    return {"status": "ranked", "candidates": scored}


def main() -> int:
    parser = argparse.ArgumentParser(description="score prforge candidate issues from json")
    parser.add_argument("input", help="json file containing an array of candidate issue objects")
    args = parser.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        print(f"error: could not read candidates: {exc}", file=sys.stderr)
        return 1
    if not isinstance(data, list):
        print("error: candidate input must be a json array", file=sys.stderr)
        return 1
    print(json.dumps(rank_candidates(data), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
