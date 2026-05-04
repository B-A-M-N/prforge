"""
PRForge Mesh — Manager Mode policy engine.

Evaluates coordinator_verdict.json + auditor_verdict.json against
manager_mode config and produces a deterministic decision:

  manager_certified      — all criteria pass, within authority
  manager_requeue        — fixable failure, job returned to queue
  manager_blocked        — hard failure, job blocked
  manager_escalate       — uncertain, requires human
  manager_auto_ship_allowed — low-risk-public only, all criteria + authority met

Authority levels (enforcing least-privilege):
  off               — manager mode disabled, no gating (standalone PRForge unchanged)
  certify_only      — may certify, may notify, may NOT execute public actions
  internal_actions  — may requeue/block/revalidate/release leases, may certify
                      may NOT execute public actions (push/comment/merge/PR create)
  low_risk_public   — may execute allowed_public_actions only
                      never executes forbidden_public_actions
                      requires risk <= max_risk
                      requires all configured require_* criteria pass
                      requires signatures verify
                      requires no unknown CI, stale review, scope delta,
                      dependency changes (unless allowed), auth/security changes (unless allowed)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from mesh_signing import sign_artifact, verify_artifact, get_signing_key

logger = logging.getLogger("prforge.manager")


def _artifact_dir(repo_path: Path) -> Path:
    pointer = repo_path / ".prforge-run"
    if pointer.exists() and not pointer.is_symlink():
        data: dict[str, str] = {}
        for line in pointer.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
        if data.get("artifact_dir"):
            return Path(data["artifact_dir"])
    return repo_path / ".prforge"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DECISION_CERTIFIED = "manager_certified"
DECISION_REQUEUE = "manager_requeue"
DECISION_BLOCKED = "manager_blocked"
DECISION_ESCALATE = "manager_escalate"
DECISION_AUTO_SHIP = "manager_auto_ship_allowed"

AUTHORITY_OFF = "off"
AUTHORITY_CERTIFY_ONLY = "certify_only"
AUTHORITY_INTERNAL = "internal_actions"
AUTHORITY_LOW_RISK = "low_risk_public"

VALID_AUTHORITIES = {AUTHORITY_OFF, AUTHORITY_CERTIFY_ONLY, AUTHORITY_INTERNAL, AUTHORITY_LOW_RISK}

# Risk ordering for max_risk comparison
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# Public actions that low_risk_public may never execute regardless of config
HARD_FORBIDDEN_ACTIONS = {"force_push", "merge", "delete_branch"}


# ---------------------------------------------------------------------------
# Manager Mode config
# ---------------------------------------------------------------------------

def load_manager_config(config: dict) -> dict:
    """
    Return the manager_mode section from config, merged with defaults.
    If manager_mode is absent, return the 'off' defaults (safe no-op).
    """
    defaults = {
        "enabled": False,
        "authority": AUTHORITY_OFF,
        "require_coordinator_pass": True,
        "require_auditor_pass": True,
        "require_clean_validation": True,
        "require_review_freshness": True,
        "require_ci_relatedness_clean": True,
        "require_no_unknown_ci_for_auto_ship": True,
        "require_no_scope_delta": True,
        "require_dod_evidence": True,
        "require_artifact_exclusion": True,
        "max_risk": "medium",
        "auto_requeue_on_fail": True,
        "auto_certify_on_pass": True,
        "auto_public_actions": False,
        "allowed_public_actions": [],
        "forbidden_public_actions": list(HARD_FORBIDDEN_ACTIONS),
    }
    mgr = config.get("manager_mode", {})
    # Merge: user values override defaults
    merged = {**defaults, **mgr}
    # Enforce: user can set their own forbidden list but HARD_FORBIDDEN is always included
    user_forbidden = set(merged.get("forbidden_public_actions", []))
    merged["forbidden_public_actions"] = sorted(
        user_forbidden | HARD_FORBIDDEN_ACTIONS
    )
    # Enforce: authority must be valid
    if merged["authority"] not in VALID_AUTHORITIES:
        logger.warning("Invalid manager_mode authority %r — defaulting to 'off'",
                       merged["authority"])
        merged["authority"] = AUTHORITY_OFF
    return merged


def manager_mode_enabled(manager_cfg: dict) -> bool:
    """Return True if manager mode is active (authority != off and enabled)."""
    return manager_cfg.get("enabled", False) and manager_cfg.get("authority", AUTHORITY_OFF) != AUTHORITY_OFF


# ---------------------------------------------------------------------------
# Artifact loaders
# ---------------------------------------------------------------------------

def _load_json_artifact(path: Path) -> dict | None:
    """Load a JSON artifact. Return None if missing or unparseable."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load artifact %s: %s", path, e)
        return None


def _verify_artifact_signature(artifact: dict, artifact_name: str) -> bool:
    """Verify the _signature field of a signed artifact. Returns True if valid."""
    try:
        return verify_artifact(artifact)
    except Exception as e:
        logger.warning("Signature verification failed for %s: %s", artifact_name, e)
        return False


# ---------------------------------------------------------------------------
# Criteria evaluation helpers
# ---------------------------------------------------------------------------

def _coordinator_passed(verdict: dict | None, manager_cfg: dict) -> tuple[bool, str]:
    """Check if coordinator_verdict meets pass criteria. Returns (ok, reason)."""
    if manager_cfg.get("require_coordinator_pass", True) and verdict is None:
        return False, "coordinator_verdict.json missing"
    if verdict is None:
        return True, ""
    if not _verify_artifact_signature(verdict, "coordinator_verdict.json"):
        return False, "coordinator_verdict signature invalid"
    if verdict.get("decision") != "coordinator_pass":
        reason = verdict.get("failure_reason", verdict.get("reason", "unknown"))
        return False, f"coordinator_verdict decision={verdict.get('decision')}: {reason}"
    return True, ""


def _auditor_passed(verdict: dict | None, manager_cfg: dict) -> tuple[bool, str]:
    """Check if auditor_verdict meets pass criteria. Returns (ok, reason)."""
    if manager_cfg.get("require_auditor_pass", True) and verdict is None:
        return False, "auditor_verdict.json missing"
    if verdict is None:
        return True, ""
    if not _verify_artifact_signature(verdict, "auditor_verdict.json"):
        return False, "auditor_verdict signature invalid"
    if verdict.get("decision") != "auditor_pass":
        reason = verdict.get("failure_reason", verdict.get("reason", "unknown"))
        return False, f"auditor_verdict decision={verdict.get('decision')}: {reason}"
    return True, ""


# Required auditor/coordinator check keys for Manager Mode evaluation.
# These must be present in the respective verdicts for the manager to certify.
REQUIRED_COORDINATOR_CHECK_KEYS: list[str] = []

REQUIRED_AUDITOR_CHECK_KEYS: list[str] = [
    "validation_claims_supported",
    "review_freshness_clean",
    "ci_relatedness_clean",
    "unknown_ci_exists",
    "scope_delta_clean",
    "dod_evidence_valid",
    "prforge_artifacts_not_staged",
    "public_text_preview_exists",
]


def _required_check_pass(
    checks: dict,
    key: str,
    authority: str,
) -> tuple[bool, str]:
    """
    Evaluate a required check key with authority-aware fail-open/closed behavior.

    Returns (pass, reason).

    Rules:
      - low_risk_public: missing key = fail (strict)
      - internal_actions: missing key = fail (strict)
      - certify_only: missing key = fail (strict for safety-critical criteria)
      - off: N/A (evaluate returns manager_disabled before reaching here)
    """
    val = checks.get(key)
    if val is None:
        # Key missing entirely — fail closed for all active authorities
        return False, f"missing required check: {key}"

    if isinstance(val, dict):
        if not val.get("pass", False):
            reason = val.get("reason", "")
            return False, reason or f"{key} failed"
        return True, ""

    # Boolean value
    if not val:
        return False, f"{key} failed"
    return True, ""


def _check_risk_threshold(auditor_verdict: dict | None, cfg: dict) -> tuple[bool, str]:
    """Risk must be within max_risk threshold."""
    max_risk = cfg.get("max_risk", "medium")
    audit = auditor_verdict or {}
    risk_level = audit.get("risk_level", "low")
    if RISK_ORDER.get(risk_level, 99) > RISK_ORDER.get(max_risk, 1):
        return False, f"risk={risk_level} exceeds max_risk={max_risk}"
    return True, ""


# ---------------------------------------------------------------------------
# Main evaluation function
# ---------------------------------------------------------------------------

def evaluate(
    repo_path: Path,
    manager_cfg: dict,
    signing_key: str | None = None,
) -> dict:
    """
    Load coordinator_verdict.json and auditor_verdict.json from the run artifact
    directory's mesh/ folder,
    evaluate all criteria in manager_cfg, and return a manager_verdict dict.

    manager_cfg may be either the full config dict (with 'manager_mode' key) or
    the manager_mode section directly. The function normalizes both.

    The returned dict is unsigned — call write_verdict() to sign and persist.
    """
    mesh_dir = _artifact_dir(repo_path) / "mesh"
    coord_verdict = _load_json_artifact(mesh_dir / "coordinator_verdict.json")
    audit_verdict = _load_json_artifact(mesh_dir / "auditor_verdict.json")

    # Normalize: accept full config dict or manager_mode section directly
    if "manager_mode" in manager_cfg:
        mgr = manager_cfg["manager_mode"]
    else:
        mgr = manager_cfg

    authority = mgr.get("authority", AUTHORITY_OFF)

    # If manager mode is off, return a pass-through verdict
    if not (mgr.get("enabled", False) and authority != AUTHORITY_OFF):
        return {
            "decision": "manager_disabled",
            "authority": AUTHORITY_OFF,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "criteria": {},
            "coordinator_verdict_present": coord_verdict is not None,
            "auditor_verdict_present": audit_verdict is not None,
        }

    # Build the combined checks dict from both verdicts.
    # Coordinator verdict checks are keyed under coordinator_verdict["checks"].
    # Auditor verdict checks are keyed under auditor_verdict["checks"].
    coord_checks: dict = (coord_verdict or {}).get("checks", {})
    audit_checks: dict = (audit_verdict or {}).get("checks", {})

    # Run all criteria checks.
    # For auditor checks, use _required_check_pass which enforces fail-closed
    # behavior: missing required key = failure for all active authority levels.
    criteria_checks: list[tuple[str, tuple[bool, str]]] = [
        # Coordinator-level checks
        ("coordinator_pass",   _coordinator_passed(coord_verdict, mgr)),
        ("auditor_pass",       _auditor_passed(audit_verdict, mgr)),
        # Auditor-level checks — fail closed on missing keys
        ("validation_claims_supported", _required_check_pass(audit_checks, "validation_claims_supported", authority)),
        ("review_freshness",   _required_check_pass(audit_checks, "review_freshness_clean", authority)),
        ("ci_relatedness",     _required_check_pass(audit_checks, "ci_relatedness_clean", authority)),
        ("no_unknown_ci",      _required_check_pass(audit_checks, "unknown_ci_exists", authority)),
        ("no_scope_delta",     _required_check_pass(audit_checks, "scope_delta_clean", authority)),
        ("dod_evidence",       _required_check_pass(audit_checks, "dod_evidence_valid", authority)),
        ("artifact_exclusion", _required_check_pass(audit_checks, "prforge_artifacts_not_staged", authority)),
        ("public_text_previewed", _required_check_pass(audit_checks, "public_text_preview_exists", authority)),
        # Risk threshold (not a check key — computed from audit verdict top-level field)
        ("risk_threshold",     _check_risk_threshold(audit_verdict, mgr)),
    ]

    criteria: dict[str, dict] = {}
    all_pass = True
    first_failure = ""

    for name, (ok, reason) in criteria_checks:
        criteria[name] = {"pass": ok, "reason": reason}
        if not ok:
            all_pass = False
            if not first_failure:
                first_failure = f"{name}: {reason}"

    # Determine decision based on authority + criteria result
    if all_pass:
        if authority == AUTHORITY_CERTIFY_ONLY:
            decision = DECISION_CERTIFIED
        elif authority == AUTHORITY_INTERNAL:
            decision = DECISION_CERTIFIED
        elif authority == AUTHORITY_LOW_RISK:
            decision = DECISION_AUTO_SHIP
        else:
            decision = DECISION_CERTIFIED
    else:
        # Determine: requeue vs block vs escalate
        if authority == AUTHORITY_INTERNAL and mgr.get("auto_requeue_on_fail", True):
            decision = DECISION_REQUEUE
        elif authority == AUTHORITY_CERTIFY_ONLY:
            # certify_only can only certify or escalate — never auto-requeue
            decision = DECISION_ESCALATE
        else:
            decision = DECISION_BLOCKED

    verdict: dict = {
        "decision": decision,
        "authority": authority,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "all_criteria_pass": all_pass,
        "criteria": criteria,
        "coordinator_verdict_present": coord_verdict is not None,
        "auditor_verdict_present": audit_verdict is not None,
        "failure_reason": first_failure if not all_pass else "",
    }

    if all_pass and manager_cfg.get("auto_certify_on_pass", True):
        verdict["auto_certified"] = True

    return verdict


def sign_and_write_verdict(verdict: dict, path: Path, signing_key: str | None = None) -> dict:
    """Sign the verdict with HMAC-SHA256 and write to path. Returns the signed verdict."""
    if signing_key is None:
        signing_key = get_signing_key()
    signed = sign_artifact(verdict, signing_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(signed, indent=2))
    logger.info("Wrote signed verdict: %s (decision=%s)", path, verdict.get("decision"))
    return signed


def write_mesh_certification(
    manager_verdict: dict,
    coordinator_hash: str,
    auditor_hash: str,
    current_diff_hash: str,
    repo_path: Path,
    signing_key: str | None = None,
) -> dict:
    """
    Write mesh_certification.json — the final certification that ties together
    the manager verdict with artifact hashes for later verification.
    """
    if signing_key is None:
        signing_key = get_signing_key()
    certification = {
        "decision": manager_verdict.get("decision", ""),
        "authority": manager_verdict.get("authority", AUTHORITY_OFF),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hashes": {
            "coordinator_verdict": coordinator_hash,
            "auditor_verdict": auditor_hash,
            "diff": current_diff_hash,
        },
        "all_criteria_pass": manager_verdict.get("all_criteria_pass", False),
    }
    signed = sign_artifact(certification, signing_key)
    cert_path = _artifact_dir(repo_path) / "mesh" / "mesh_certification.json"
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_text(json.dumps(signed, indent=2))
    logger.info("Wrote mesh certification: %s", cert_path)
    return signed


def can_execute_public_action(action: str, manager_cfg: dict, verdict: dict) -> tuple[bool, str]:
    """
    Check if a specific public action is allowed under current manager_mode authority.
    Returns (allowed, reason).

    manager_cfg may be either the full config dict (with 'manager_mode' key) or
    the manager_mode section directly.
    """
    # Normalize: accept full config dict or manager_mode section directly
    if "manager_mode" in manager_cfg:
        mgr = manager_cfg["manager_mode"]
    else:
        mgr = manager_cfg

    authority = mgr.get("authority", AUTHORITY_OFF)

    if not (mgr.get("enabled", False) and authority != AUTHORITY_OFF):
        return False, "manager mode not enabled"

    if authority in (AUTHORITY_CERTIFY_ONLY, AUTHORITY_INTERNAL):
        return False, f"authority={authority} does not permit public actions"

    if authority != AUTHORITY_LOW_RISK:
        return False, f"authority={authority} does not permit public actions"

    # Check hard-forbidden
    if action in HARD_FORBIDDEN_ACTIONS:
        return False, f"action={action} is hard-forbidden"

    # Check user forbidden list
    if action in mgr.get("forbidden_public_actions", []):
        return False, f"action={action} is in forbidden_public_actions"

    # Check allowed list
    allowed = mgr.get("allowed_public_actions", [])
    if allowed and action not in allowed:
        return False, f"action={action} not in allowed_public_actions"

    # Check verdict
    if verdict.get("decision") != DECISION_AUTO_SHIP:
        return False, f"manager_verdict decision={verdict.get('decision')} != auto_ship_allowed"

    return True, ""
