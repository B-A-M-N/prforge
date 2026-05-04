"""
PRForge policy engine.

Combines deterministic gates with optional local/mesh intel context. Intel can
increase caution, choose redirect paths, or escalate review. It never bypasses
deterministic safety for public actions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DETERMINISTIC_PUBLIC_ACTIONS = {
    "push",
    "force_push",
    "create_pr",
    "post_comment",
    "request_review",
    "merge",
    "delete_branch",
}


DEFAULT_RISK_REDIRECTS = {
    "missing_regression_test": "VALIDATION_REPAIR",
    "missing_review_refresh": "REVIEW_REFRESH",
    "unexpected_file": "SCOPE_RECONCILE",
    "artifact_leak": "ARTIFACT_REPAIR",
    "contract_mismatch": "PLAN_UPDATE",
    "stale_state": "STATE_SYNC_REPAIR",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        return default
    return default


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _changed_files(repo: Path) -> list[str]:
    files: set[str] = set()
    for args in (
        ["diff", "--name-only"],
        ["diff", "--cached", "--name-only"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        try:
            result = _git(repo, args)
            if result.returncode == 0:
                files.update(line.strip() for line in result.stdout.splitlines() if line.strip())
        except Exception:
            pass
    return sorted(files)


def _artifact_patterns(path: str) -> bool:
    parts = path.split("/")
    return (
        path == ".prforge-run"
        or path.startswith(".prforge/")
        or path.startswith(".prforge-")
        or ".prforge" in parts
    )


def _load_policy_bundle(run_dir: Path) -> dict:
    bundle = _read_json(run_dir / "policy_bundle.json", {})
    if not isinstance(bundle, dict):
        return {}
    return bundle


def _load_intel_signals(run_dir: Path) -> list[dict]:
    signals: list[dict] = []
    for rel in ("intel/risk_signals.json", "intel/mesh_risk_signals.json"):
        data = _read_json(run_dir / rel, [])
        if isinstance(data, list):
            signals.extend(x for x in data if isinstance(x, dict))
        elif isinstance(data, dict):
            items = data.get("signals") or data.get("risks") or []
            if isinstance(items, list):
                signals.extend(x for x in items if isinstance(x, dict))
    return signals


def _load_intel_context(run_dir: Path) -> str:
    parts = []
    for rel in ("intel_context.md", "intel/local_context.md", "intel/mesh_context.md"):
        path = run_dir / rel
        try:
            if path.exists():
                parts.append(path.read_text())
        except Exception:
            pass
    return "\n\n".join(parts)


def _lexical_risk_from_context(event: str, phase: str, changed: list[str], context: str) -> list[dict]:
    """Cheap local fallback when embeddings/reranker are unavailable."""
    if not context.strip():
        return []

    haystack = context.lower()
    joined_files = " ".join(changed).lower()
    signals: list[dict] = []
    patterns = [
        ("missing_regression_test", ["missing regression", "regression test", "malformed", "edge case"]),
        ("missing_review_refresh", ["new maintainer comment", "stale review", "review refresh"]),
        ("contract_mismatch", ["scope mismatch", "contract mismatch", "patch plan"]),
    ]
    for risk_type, needles in patterns:
        score = 0.0
        matched = []
        for needle in needles:
            if needle in haystack or needle in joined_files:
                score += 0.22
                matched.append(needle)
        if matched:
            signals.append({
                "source": "local_lexical_fallback",
                "risk_type": risk_type,
                "risk_score": min(score + 0.35, 0.95),
                "reason": f"intel context mentions: {', '.join(matched)}",
                "recommended_redirect": DEFAULT_RISK_REDIRECTS.get(risk_type),
            })
    return signals


def _fastembed_query(event: str, phase: str, run_dir: Path, changed: list[str]) -> tuple[list[dict], dict]:
    try:
        import intel_engine
    except Exception as exc:
        return [], {
            "enabled": False,
            "provider": "fastembed",
            "status": "import_failed",
            "error": str(exc),
        }

    caps = intel_engine.load_capabilities()
    if not caps.get("ready"):
        return [], {
            "enabled": False,
            "provider": "fastembed",
            "status": "preflight_not_ready",
            "capabilities_path": str(intel_engine.capabilities_path()),
            "errors": caps.get("errors", []),
        }

    query = (
        f"PRForge policy event={event} phase={phase}. "
        f"Changed files: {', '.join(changed[:40])}. "
        "Which prior artifacts predict maintainer objection, missing validation, "
        "scope risk, or regression-test gaps for this exact event?"
    )
    try:
        result = intel_engine.query_run(run_dir, query=query, top_k=5, recall_k=50)
        return result.get("risk_signals", []), {
            "enabled": True,
            "provider": "fastembed",
            "status": "ready",
            "embedding_model": caps.get("embedding_model", ""),
            "reranker_model": caps.get("reranker_model", ""),
            "matches": len(result.get("matches", [])),
            "risk_signal_path": result.get("risk_signal_path", ""),
        }
    except Exception as exc:
        return [], {
            "enabled": True,
            "provider": "fastembed",
            "status": "runtime_failed",
            "error": str(exc),
            "fail_safe": "deterministic_gates_remain_active",
        }


def _normalize_score(value: Any) -> float:
    try:
        score = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(score, 1.0))


def _top_risks(signals: list[dict]) -> list[dict]:
    normalized = []
    for signal in signals:
        score = _normalize_score(signal.get("risk_score", signal.get("score", 0)))
        if score <= 0:
            continue
        item = dict(signal)
        item["risk_score"] = score
        normalized.append(item)
    return sorted(normalized, key=lambda x: x["risk_score"], reverse=True)[:5]


def _deterministic_checks(event: str, phase: str, run_dir: Path, repo: Path | None) -> list[dict]:
    checks: list[dict] = []
    state = _read_json(run_dir / "state.json", {})
    bundle = _load_policy_bundle(run_dir)
    allowed_files = set(bundle.get("allowed_files") or state.get("scope", {}).get("allowed_files") or [])

    if event == "phase_exit" and phase == "IMPLEMENT" and repo is not None:
        changed = _changed_files(repo)
        unexpected = []
        if allowed_files:
            for path in changed:
                if not any(path == pat or path.startswith(pat.rstrip("/") + "/") or pat in path for pat in allowed_files):
                    unexpected.append(path)
        if unexpected:
            checks.append({
                "pass": False,
                "reason": "unexpected_file",
                "details": unexpected,
                "redirect_state": "SCOPE_RECONCILE",
                "required_next_action": "Reconcile changed files against patch_plan.md and contract.md.",
            })

        if state.get("dod", {}).get("items_total") and not state.get("dod", {}).get("evidence_verified", False):
            checks.append({
                "pass": False,
                "reason": "dod_evidence_missing",
                "redirect_state": "PLAN_UPDATE",
                "required_next_action": "Update DoD evidence before leaving IMPLEMENT.",
            })

    if event in ("public_action", "push", "post_comment", "create_pr"):
        approval = state.get("approval", {})
        if not approval.get("approval_id"):
            checks.append({
                "pass": False,
                "reason": "public_action_without_approval",
                "redirect_state": "ARTIFACT_REPAIR",
                "required_next_action": "Regenerate approval.md and obtain explicit user approval.",
            })

    if repo is not None:
        try:
            staged = _git(repo, ["diff", "--cached", "--name-only"])
            tracked = _git(repo, ["ls-files"])
            artifact_hits = []
            if staged.returncode == 0:
                artifact_hits.extend(p for p in staged.stdout.splitlines() if _artifact_patterns(p))
            if tracked.returncode == 0:
                artifact_hits.extend(p for p in tracked.stdout.splitlines() if _artifact_patterns(p))
            if artifact_hits:
                checks.append({
                    "pass": False,
                    "reason": "artifact_leak",
                    "details": sorted(set(artifact_hits)),
                    "redirect_state": "ARTIFACT_REPAIR",
                    "required_next_action": "Remove PRForge artifacts from git index before continuing.",
                })
        except Exception:
            pass

    return checks


def _policy_bundle_status(run_dir: Path) -> dict:
    bundle = _load_policy_bundle(run_dir)
    if not bundle:
        return {"available": False, "valid": False, "reason": "missing_policy_bundle"}
    expires_at = bundle.get("expires_at")
    if expires_at:
        try:
            expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if expiry < datetime.now(timezone.utc):
                return {"available": True, "valid": False, "reason": "expired_policy_bundle"}
        except ValueError:
            return {"available": True, "valid": False, "reason": "invalid_policy_bundle_expiry"}
    return {
        "available": True,
        "valid": True,
        "policy_version": bundle.get("policy_version", ""),
        "risk_rules_hash": bundle.get("risk_rules_hash", ""),
        "intel_context_hash": bundle.get("intel_context_hash", ""),
    }


def check_policy(event: str, phase: str, run_dir: Path, repo: Path | None = None) -> dict:
    run_dir = run_dir.resolve()
    repo = repo.resolve() if repo else None

    deterministic = _deterministic_checks(event, phase, run_dir, repo)
    context = _load_intel_context(run_dir)
    changed = _changed_files(repo) if repo else []
    signals = _load_intel_signals(run_dir)
    fastembed_signals, fastembed_status = _fastembed_query(event, phase, run_dir, changed)
    signals.extend(fastembed_signals)
    signals.extend(_lexical_risk_from_context(event, phase, changed, context))
    top = _top_risks(signals)
    bundle_status = _policy_bundle_status(run_dir)

    failed = [c for c in deterministic if not c.get("pass", True)]
    if failed:
        first = failed[0]
        decision = "redirect_recoverable"
        redirect_state = first.get("redirect_state", "STATE_SYNC_REPAIR")
        reason = first.get("reason", "deterministic_gate_failed")
        required = first.get("required_next_action", "Resolve deterministic policy gate.")
    elif top and top[0]["risk_score"] >= 0.80:
        decision = "redirect_recoverable"
        redirect_state = top[0].get("recommended_redirect") or DEFAULT_RISK_REDIRECTS.get(top[0].get("risk_type"), "VALIDATION_REPAIR")
        reason = top[0].get("risk_type", "high_intel_risk")
        required = top[0].get("required_next_action") or top[0].get("reason") or "Address high-risk intel finding."
    elif top and top[0]["risk_score"] >= 0.60:
        decision = "allow_with_warning"
        redirect_state = ""
        reason = top[0].get("risk_type", "medium_intel_risk")
        required = top[0].get("reason", "Review medium-risk intel finding.")
    else:
        decision = "allow"
        redirect_state = ""
        reason = "policy_pass"
        required = ""

    if event in ("public_action", "push", "post_comment", "create_pr") and decision == "allow":
        # Adaptive policy never grants public authority. It can only confirm no
        # extra caution beyond deterministic approval checks.
        decision = "allow_deterministic_checks_only"

    return {
        "decision": decision,
        "redirect_state": redirect_state,
        "reason": reason,
        "required_next_action": required,
        "event": event,
        "phase": phase,
        "run_dir": str(run_dir),
        "repo": str(repo) if repo else "",
        "deterministic_checks": deterministic,
        "policy_bundle": bundle_status,
        "intel": {
            "available": bool(context or signals),
            "adaptive_enforcement": bool(context or signals),
            "fastembed": fastembed_status,
            "top_risks": top,
            "context_hash": _sha256_text(context) if context else "",
        },
        "fail_safe": {
            "deterministic_gates_remain_active": True,
            "intel_may_bypass_public_actions": False,
            "local_policy_scope": "current_run_only",
        },
        "checked_at": _now(),
    }


def write_decision_artifacts(run_dir: Path, decision: dict) -> None:
    _write_json(run_dir / "policy" / "last_decision.json", decision)
    top = decision.get("intel", {}).get("top_risks", [])
    if top:
        lines = ["# PRForge Intel Context", ""]
        for idx, risk in enumerate(top, 1):
            lines.append(f"{idx}. `{risk.get('risk_type', 'risk')}` score={risk.get('risk_score', 0):.2f}")
            if risk.get("reason"):
                lines.append(f"   - {risk['reason']}")
            if risk.get("supporting_artifacts"):
                lines.append(f"   - supporting_artifacts: {risk['supporting_artifacts']}")
        (run_dir / "intel_context.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PRForge policy engine")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--event", required=True)
    check.add_argument("--phase", required=True)
    check.add_argument("--run-dir", required=True)
    check.add_argument("--repo", default="")
    check.add_argument("--write", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "check":
        decision = check_policy(
            event=args.event,
            phase=args.phase,
            run_dir=Path(args.run_dir),
            repo=Path(args.repo) if args.repo else None,
        )
        if args.write:
            write_decision_artifacts(Path(args.run_dir), decision)
        print(json.dumps(decision, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
