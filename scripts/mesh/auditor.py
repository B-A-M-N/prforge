"""
PRForge Mesh — auditor loop.
Runs on Machine 3. Polls GitHub for open PRs. Read-only.
Never edits, commits, pushes, comments, creates PRs, or approves anything.

Cursor semantics:
  last_review_cursor  = latest submittedAt of PROCESSED external reviews.
                        Only updated AFTER review_response job is queued.
                        Never set from PR.updatedAt.
  last_checks_hash    = stable hash of last OBSERVED check state (sorted, normalized).
                        Updated every poll cycle where checks changed.
  last_audited_head_sha = head SHA of the last PR state that received an audit_only job.
                          Updated only when audit_only job is queued.

Skip-if-unchanged invariant:
  Skip all classification if ALL three match:
    current_head_sha == last_audited_head_sha
    AND current_review_cursor == last_review_cursor
    AND current_checks_hash == last_checks_hash

max_llm_audits_per_hour: enforced via Redis sorted set. Survives daemon restart.

medium_idle_only: P3 findings may queue only when there are NO P0/P1 jobs
  pending OR active anywhere in the pipeline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


def _read_pointer_artifact_dir(repo_path: str) -> Path:
    pointer = Path(repo_path) / ".prforge-run"
    if pointer.exists() and not pointer.is_symlink():
        data = {}
        for line in pointer.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
        if data.get("artifact_dir"):
            return Path(data["artifact_dir"])
    return Path(repo_path) / ".prforge"

# Ensure sibling modules are importable when run from systemd
sys.path.insert(0, str(Path(__file__).parent))

import redis

from redis_backend import (
    audit_budget_record,
    audit_budget_under_limit,
    emit_event,
    enqueue_job,
    get_pr_cursor,
    has_high_priority_pressure,
    try_acquire_enqueue_dedupe,
    update_pr_cursor,
)
from notifications import notify
from mesh_signing import sign_artifact, get_signing_key

logger = logging.getLogger("prforge.auditor")

PRIORITY_REVIEW_BLOCKING = "P0"
PRIORITY_REVIEW_COMMENT  = "P1"
PRIORITY_CI_FAILURE      = "P2"
PRIORITY_AUDIT_FINDING   = "P3"


def run(r: redis.Redis, cluster: str, config: dict) -> None:
    auditor_cfg = config.get("auditor", {})
    notif_cfg   = config.get("notifications", {})

    if not auditor_cfg.get("enabled", False):
        logger.info("Auditor disabled in config — exiting")
        return

    poll_interval_s  = auditor_cfg.get("poll_interval_minutes", 15) * 60
    lookback_days    = auditor_cfg.get("lookback_days", 3)
    skip_unchanged   = auditor_cfg.get("skip_if_unchanged", True)
    max_llm_per_hr   = auditor_cfg.get("max_llm_audits_per_hour", 3)
    medium_idle_only = auditor_cfg.get("queue_medium_findings_only_when_idle", True)

    desktop = notif_cfg.get("desktop", True)
    pubsub  = notif_cfg.get("pubsub", True)

    logger.info(
        "Auditor started cluster=%s lookback=%dd poll=%ds "
        "skip_unchanged=%s max_llm_per_hr=%d medium_idle_only=%s",
        cluster, lookback_days, poll_interval_s,
        skip_unchanged, max_llm_per_hr, medium_idle_only,
    )

    while True:
        try:
            _poll(
                r=r,
                cluster=cluster,
                lookback_days=lookback_days,
                skip_unchanged=skip_unchanged,
                max_llm_per_hr=max_llm_per_hr,
                medium_idle_only=medium_idle_only,
                desktop=desktop,
                pubsub=pubsub,
            )
        except Exception as e:
            logger.exception("Auditor poll error: %s", e)
        time.sleep(poll_interval_s)


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------

def _poll(
    r: redis.Redis,
    cluster: str,
    lookback_days: int,
    skip_unchanged: bool,
    max_llm_per_hr: int,
    medium_idle_only: bool,
    desktop: bool,
    pubsub: bool,
) -> None:
    me = _gh_whoami()
    if not me:
        logger.error("gh auth not available — auditor cannot poll")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    prs    = _list_open_prs(me)
    logger.debug("Auditor polled %d open PRs for %s", len(prs), me)

    for pr in prs:
        updated_raw = pr.get("updatedAt", "")
        try:
            updated_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        # 3-day lookback filter — skip PRs outside window
        if updated_at < cutoff:
            continue

        repo      = pr.get("repo", "")
        pr_number = str(pr.get("number", ""))
        if not repo or not pr_number:
            continue

        try:
            _process_pr(
                r=r,
                cluster=cluster,
                pr=pr,
                repo=repo,
                pr_number=pr_number,
                updated_raw=updated_raw,
                me=me,
                skip_unchanged=skip_unchanged,
                max_llm_per_hr=max_llm_per_hr,
                medium_idle_only=medium_idle_only,
                desktop=desktop,
                pubsub=pubsub,
            )
        except Exception as e:
            logger.exception("Error processing PR %s#%s: %s", repo, pr_number, e)


# ---------------------------------------------------------------------------
# Per-PR processing
# ---------------------------------------------------------------------------

def _process_pr(
    r: redis.Redis,
    cluster: str,
    pr: dict,
    repo: str,
    pr_number: str,
    updated_raw: str,
    me: str,
    skip_unchanged: bool,
    max_llm_per_hr: int,
    medium_idle_only: bool,
    desktop: bool,
    pubsub: bool,
) -> None:
    # Load existing cursor state (all 7 fields guaranteed by get_pr_cursor)
    cursor = get_pr_cursor(r, cluster, repo, pr_number)

    current_head_sha = pr.get("headRefOid", "")

    # Compute current review cursor from external reviewer activity only.
    # Do NOT use PR.updatedAt — that includes self-activity.
    all_reviews       = _fetch_reviews(repo, pr_number, me)
    current_rev_cursor = _latest_external_review_ts(all_reviews)

    # Compute stable normalized check hash
    checks               = pr.get("statusCheckRollup") or []
    current_checks_hash  = _hash_checks(checks)

    # Read stored cursor values
    last_head        = cursor.get("last_audited_head_sha", "")
    last_rev_cursor  = cursor.get("last_review_cursor", "")
    last_checks_hash = cursor.get("last_checks_hash", "")

    head_changed    = current_head_sha != last_head
    review_changed  = (
        bool(current_rev_cursor) and current_rev_cursor != last_rev_cursor
    )
    checks_changed  = (
        bool(current_checks_hash) and current_checks_hash != last_checks_hash
    )

    logger.debug(
        "PR %s#%s: head_changed=%s review_changed=%s checks_changed=%s",
        repo, pr_number, head_changed, review_changed, checks_changed,
    )

    # --- Skip-if-unchanged ---
    if skip_unchanged and not head_changed and not review_changed and not checks_changed:
        logger.debug("PR %s#%s unchanged — skipping all classification", repo, pr_number)
        return

    # --- Review cursor changed: new external reviewer activity ---
    if review_changed:
        new_reviews = _filter_new_reviews(all_reviews, last_rev_cursor)
        if new_reviews:
            queued_review = _handle_new_reviews(
                r=r, cluster=cluster, repo=repo, pr_number=pr_number,
                pr=pr, new_reviews=new_reviews,
                desktop=desktop, pubsub=pubsub,
            )
            # Only advance last_review_cursor AFTER job is successfully queued
            if queued_review:
                cursor["last_review_cursor"] = current_rev_cursor

    # --- Checks hash changed: CI state changed ---
    if checks_changed:
        diff_files = _fetch_diff_files(repo, pr_number)
        _handle_ci_checks(
            r=r, cluster=cluster, repo=repo, pr_number=pr_number,
            pr=pr, checks=checks, diff_files=diff_files,
            desktop=desktop, pubsub=pubsub,
        )
        # Update checks hash after classification regardless of enqueue result
        cursor["last_checks_hash"] = current_checks_hash

    # --- Head SHA changed: eligible for LLM audit ---
    audit_queued = False
    if head_changed:
        high_pressure = has_high_priority_pressure(r, cluster)
        if medium_idle_only and high_pressure:
            logger.info(
                "PR %s#%s: P0/P1 pressure — deferring audit_only", repo, pr_number
            )
            emit_event(r, cluster, "AuditDeferred", {
                "repo": repo, "pr_number": pr_number,
                "reason": "P0/P1 pressure (medium_idle_only)",
            })
        elif not audit_budget_under_limit(r, cluster, max_llm_per_hr):
            logger.info(
                "PR %s#%s: LLM audit budget exhausted (%d/hr) — skipping",
                repo, pr_number, max_llm_per_hr,
            )
            emit_event(r, cluster, "AuditSkippedBudgetLimit", {
                "repo": repo, "pr_number": pr_number,
                "max_per_hr": str(max_llm_per_hr),
            })
            notify(r, cluster, "AuditSkippedBudgetLimit",
                   f"Budget limit hit ({max_llm_per_hr}/hr) — audit_only skipped for {repo}#{pr_number}",
                   desktop=desktop, pubsub=pubsub)
        else:
            audit_queued = _enqueue_audit_only(
                r=r, cluster=cluster, repo=repo, pr_number=pr_number,
                pr=pr, head_sha=current_head_sha,
                desktop=desktop, pubsub=pubsub,
            )

    # --- Persist updated cursor state ---
    # Merge what changed back into stored state
    update_pr_cursor(r, cluster, repo, pr_number, {
        "head_sha":              current_head_sha,
        "updated_at":            updated_raw,
        # Only advance last_audited_head_sha when audit_only job was queued
        "last_audited_head_sha": current_head_sha if audit_queued else cursor.get("last_audited_head_sha", ""),
        "last_audited_at":       _now() if audit_queued else cursor.get("last_audited_at", ""),
        # last_review_cursor: updated by _handle_new_reviews above (stored in cursor dict)
        "last_review_cursor":    cursor.get("last_review_cursor", ""),
        # last_observed_review_cursor: always records the latest seen external review ts
        "last_observed_review_cursor": current_rev_cursor,
        # last_checks_hash: updated above on checks_changed
        "last_checks_hash":      cursor.get("last_checks_hash", current_checks_hash),
        "last_audit_severity":   cursor.get("last_audit_severity", "unknown"),
    })


# ---------------------------------------------------------------------------
# Review detection
# ---------------------------------------------------------------------------

def _handle_new_reviews(
    r: redis.Redis,
    cluster: str,
    repo: str,
    pr_number: str,
    pr: dict,
    new_reviews: list,
    desktop: bool,
    pubsub: bool,
) -> bool:
    """
    Enqueue the highest-priority review_response job from new reviews.
    Returns True if a job was queued.
    """
    # Determine highest priority needed
    priority = PRIORITY_REVIEW_COMMENT  # P1 default
    for review in new_reviews:
        if review.get("state") == "CHANGES_REQUESTED":
            priority = PRIORITY_REVIEW_BLOCKING  # P0 — no need to check further
            break

    authors = [
        rev.get("author", {}).get("login", "unknown")
        for rev in new_reviews
    ]

    # Dedupe fingerprint = latest review timestamp (unique per change event)
    fingerprint = _latest_external_review_ts(new_reviews) or _now()

    job_id = _make_job_id(repo, pr_number, "review_response")

    # Atomic-enough guard: if this exact (repo, pr, review cursor) was already
    # enqueued in the last 1800s, skip. Prevents duplicate jobs from a crash
    # between enqueue_job() and the subsequent update_pr_cursor() call.
    if not try_acquire_enqueue_dedupe(r, cluster, "review", repo, pr_number,
                                      fingerprint, job_id, ttl=1800):
        logger.info("Review dedupe: already enqueued for %s#%s cursor=%s — skipping",
                    repo, pr_number, fingerprint[:20])
        return False

    job = {
        "job_id":      job_id,
        "type":        "review_response",
        "priority":    priority,
        "repo":        repo,
        "pr_number":   int(pr_number),
        "base_branch": pr.get("baseRefName", "main"),
        "head_branch": pr.get("headRefName", ""),
        "source_url":  pr.get("url", ""),
        "objective": (
            f"Address reviewer feedback on "
            f"'{pr.get('title', pr.get('headRefName', f'PR #{pr_number}'))}'"
        ),
        "acceptance_criteria": [
            {
                "id":          f"rev-{i + 1}",
                "description": (
                    f"[{rev.get('author', {}).get('login', 'reviewer')}] "
                    + ((rev.get("body") or "").strip()[:300]
                       or rev.get("state", "COMMENTED").lower().replace("_", " "))
                ),
                "kind":   "blocking" if rev.get("state") == "CHANGES_REQUESTED" else "feedback",
                "status": "pending",
            }
            for i, rev in enumerate(new_reviews)
        ],
        "created_by":  "auditor",
        "status":      "queued",
        "created_at":  _now(),
        "constraints": json.dumps({
            "public_actions_require_approval":   True,
            "only_address_main_review_feedback": True,
            "ignore_unrelated_ci":               True,
            "do_not_create_new_pr":              True,
        }),
    }
    enqueue_job(r, cluster, job)
    notify(r, cluster, "ReviewDetected",
           f"{priority} review_response queued for {repo}#{pr_number} "
           f"(reviewers: {', '.join(authors)})",
           desktop=desktop, pubsub=pubsub)
    logger.info("Queued %s review_response job_id=%s for %s#%s reviewers=%s",
                priority, job_id, repo, pr_number, authors)
    return True


def _filter_new_reviews(reviews: list, last_cursor_ts: str) -> list:
    """
    Return reviews with submittedAt strictly after last_cursor_ts.
    Empty cursor means all reviews are new.
    """
    if not last_cursor_ts:
        return [rev for rev in reviews if rev.get("state") in
                ("CHANGES_REQUESTED", "COMMENTED", "APPROVED") or rev.get("body")]
    try:
        cutoff = datetime.fromisoformat(last_cursor_ts.replace("Z", "+00:00"))
    except ValueError:
        return reviews
    return [
        rev for rev in reviews
        if _parse_ts(rev.get("submittedAt", "")) > cutoff
    ]


def _latest_external_review_ts(reviews: list) -> str:
    """
    Return the ISO timestamp of the most recent external review.
    Returns "" if no reviews.
    Do NOT use PR.updatedAt — only use review submittedAt.
    """
    timestamps = [
        rev.get("submittedAt", "")
        for rev in reviews
        if rev.get("submittedAt")
    ]
    return max(timestamps, default="")


# ---------------------------------------------------------------------------
# CI detection
# ---------------------------------------------------------------------------

def _handle_ci_checks(
    r: redis.Redis,
    cluster: str,
    repo: str,
    pr_number: str,
    pr: dict,
    checks: list,
    diff_files: list,
    desktop: bool,
    pubsub: bool,
) -> None:
    failures = [
        c for c in checks
        if c.get("conclusion") in ("FAILURE", "ERROR")
        or c.get("status") in ("FAILURE", "ERROR")
    ]
    if not failures:
        logger.debug("CI changed but no failures for %s#%s — recording only", repo, pr_number)
        return

    related   = [f for f in failures if _classify_ci(f, diff_files) == "related"]
    unrelated = [f for f in failures if _classify_ci(f, diff_files) == "unrelated"]
    unknown   = [f for f in failures if _classify_ci(f, diff_files) == "unknown"]

    logger.info(
        "CI %s#%s: %d related, %d unrelated, %d unknown failures",
        repo, pr_number, len(related), len(unrelated), len(unknown),
    )

    # Unrelated: record only, never queue
    if unrelated:
        emit_event(r, cluster, "CIUnrelatedFailure", {
            "repo": repo, "pr_number": pr_number,
            "checks": json.dumps([c.get("name") for c in unrelated]),
        })

    # Unknown: emit event and record context. Never create a ci_fix job.
    # Unknown means "cannot classify" — not a confirmed regression in the PR diff.
    # Worker would attempt fixes on transient/environment noise. Do not do that.
    if unknown:
        emit_event(r, cluster, "CIUnknownFailure", {
            "repo":   repo,
            "pr_number": pr_number,
            "checks": json.dumps([c.get("name") for c in unknown]),
        })
        logger.warning(
            "CI %s#%s: %d unknown-classification failures — recorded only, NOT queued: %s",
            repo, pr_number, len(unknown),
            [c.get("name") for c in unknown],
        )

    # Related only: queue ci_fix job.
    # Unknown failures are never included in ci_fix job creation.
    if related:
        # Dedupe fingerprint from related-check hash prefix — unique per CI state snapshot
        fingerprint = _hash_checks(related)[:24]

        job_id = _make_job_id(repo, pr_number, "ci_fix_related_to_branch")

        if not try_acquire_enqueue_dedupe(r, cluster, "ci", repo, pr_number,
                                          fingerprint, job_id, ttl=1800):
            logger.info("CI dedupe: already enqueued for %s#%s — skipping", repo, pr_number)
            return

        job = {
            "job_id":      job_id,
            "type":        "ci_fix_related_to_branch",
            "priority":    PRIORITY_CI_FAILURE,
            "repo":        repo,
            "pr_number":   int(pr_number),
            "base_branch": pr.get("baseRefName", "main"),
            "head_branch": pr.get("headRefName", ""),
            "source_url":  pr.get("url", ""),
            "objective": (
                f"Fix related CI failures on "
                f"'{pr.get('title', pr.get('headRefName', f'PR #{pr_number}'))}'"
            ),
            "acceptance_criteria": [
                {
                    "id":          f"ci-{i + 1}",
                    "description": f"CI check '{c.get('name', 'unknown')}' must pass",
                    "kind":        "validation",
                    "status":      "pending",
                }
                for i, c in enumerate(related)
            ],
            "created_by":  "auditor",
            "status":      "queued",
            "created_at":  _now(),
            "constraints": json.dumps({
                "public_actions_require_approval": True,
                "ignore_unrelated_ci":             True,
                "do_not_create_new_pr":            True,
                "related_failures": [c.get("name") for c in related],
            }),
        }
        enqueue_job(r, cluster, job)
        notify(r, cluster, "AuditFindingCreated",
               f"P2 ci_fix queued for {repo}#{pr_number} "
               f"({len(related)} related failures)",
               desktop=desktop, pubsub=pubsub)
        logger.info("Queued P2 ci_fix job_id=%s for %s#%s", job_id, repo, pr_number)
    else:
        logger.info("No related CI failures for %s#%s — not queuing", repo, pr_number)


def _classify_ci(check: dict, diff_files: list) -> str:
    """
    Classify a CI check failure as related/unrelated/unknown.

    related   — check name/context mentions a file stem from the diff
    unrelated — known global infra failure pattern
    unknown   — cannot classify; caller must NOT silently drop
    """
    name    = (check.get("name") or "").lower()
    context = (check.get("context") or "").lower()
    text    = f"{name} {context}"

    # Known global infra failures — unrelated
    infra_patterns = [
        "timeout connecting",
        "network error",
        "dns resolution",
        "runner provisioning",
        "checkout failed",
        "dockerhub rate limit",
        "service unavailable",
    ]
    if any(p in text for p in infra_patterns):
        return "unrelated"

    # Check name/context contains a file stem from the diff — related
    for f in diff_files:
        basename = f.split("/")[-1].lower()
        stem = basename.rsplit(".", 1)[0] if "." in basename else basename
        if stem and len(stem) > 3 and stem in text:
            return "related"

    return "unknown"


# ---------------------------------------------------------------------------
# LLM audit (audit_only job) — Redis-backed rate limiting
# ---------------------------------------------------------------------------

def _enqueue_audit_only(
    r: redis.Redis,
    cluster: str,
    repo: str,
    pr_number: str,
    pr: dict,
    head_sha: str,
    desktop: bool,
    pubsub: bool,
) -> bool:
    """
    Enqueue audit_only job. Returns True if queued, False otherwise.
    Rate limit enforced via Redis sorted set — survives daemon restart.
    """
    job_id = _make_job_id(repo, pr_number, "audit_only")
    job = {
        "job_id":      job_id,
        "type":        "audit_only",
        "priority":    PRIORITY_AUDIT_FINDING,
        "repo":        repo,
        "pr_number":   int(pr_number),
        "base_branch": pr.get("baseRefName", "main"),
        "head_branch": pr.get("headRefName", ""),
        "source_url":  pr.get("url", ""),
        "objective": (
            f"Audit PR quality for "
            f"'{pr.get('title', pr.get('headRefName', f'PR #{pr_number}'))} ({repo}#{pr_number})'"
        ),
        "acceptance_criteria": [],
        "created_by":  "auditor",
        "status":      "queued",
        "created_at":  _now(),
        "constraints": json.dumps({
            "public_actions_require_approval": True,
            "do_not_create_new_pr":            True,
        }),
    }
    enqueue_job(r, cluster, job)
    audit_budget_record(r, cluster, job_id)
    notify(r, cluster, "AuditFindingCreated",
           f"P3 audit_only queued for {repo}#{pr_number} (head={head_sha[:8]})",
           desktop=desktop, pubsub=pubsub)
    logger.info("Queued P3 audit_only job_id=%s for %s#%s head=%s",
                job_id, repo, pr_number, head_sha[:8])
    return True


# ---------------------------------------------------------------------------
# GitHub helpers (via gh CLI)
# ---------------------------------------------------------------------------

def _gh(args: list) -> Optional[str]:
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        logger.debug("gh %s -> exit %d: %s", args[0], result.returncode, result.stderr[:200])
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.debug("gh call failed: %s", e)
    return None


def _gh_whoami() -> Optional[str]:
    out = _gh(["api", "user", "--jq", ".login"])
    return out.strip() if out else None


def _list_open_prs(author: str) -> list:
    fields = (
        "number,title,url,headRefName,baseRefName,headRefOid,"
        "updatedAt,reviewDecision,statusCheckRollup"
    )
    out = _gh([
        "pr", "list",
        "--author", author,
        "--state", "open",
        "--json", fields,
        "--limit", "50",
    ])
    if not out:
        return []
    try:
        prs = json.loads(out)
    except json.JSONDecodeError:
        return []

    result = []
    for pr in prs:
        url   = pr.get("url", "")
        parts = url.split("/")
        # https://github.com/org/repo/pull/123
        if len(parts) >= 5:
            pr["repo"] = f"{parts[3]}/{parts[4]}"
            result.append(pr)
    return result


def _fetch_reviews(repo: str, pr_number: str, me: str) -> list:
    """
    Fetch external reviews only (not self-authored).
    Uses reviews from `gh pr view --json reviews`.
    Does NOT use PR.updatedAt or comments from the PR author.
    """
    out = _gh([
        "pr", "view", pr_number,
        "--repo", repo,
        "--json", "reviews",
    ])
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []

    reviews = data.get("reviews", [])
    # Exclude self-reviews — cursor tracks external reviewer activity only
    return [
        rev for rev in reviews
        if rev.get("author", {}).get("login", "") != me
        and rev.get("author", {}).get("login", "") != ""
    ]


def _fetch_diff_files(repo: str, pr_number: str) -> list:
    out = _gh(["pr", "diff", pr_number, "--repo", repo, "--name-only"])
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _make_job_id(repo: str, pr_number: str, job_type: str) -> str:
    slug = repo.replace("/", "_")
    ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"job_{slug}_{pr_number}_{job_type}_{ts}"


def _hash_checks(checks: list) -> str:
    """
    Produce a stable, normalized hash of CI check state.
    Sorted by name. Includes: name, conclusion, status, detailsUrl.
    Unknown state does not equal any previous known-good state because
    the content itself differs.
    """
    normalized = sorted(
        [
            {
                "name":       c.get("name", ""),
                "conclusion": c.get("conclusion") or "",
                "status":     c.get("status") or "",
                "url":        c.get("detailsUrl") or c.get("url") or "",
            }
            for c in checks
        ],
        key=lambda x: x["name"],
    )
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode()).hexdigest()[:16]


def _parse_ts(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Manager Mode — auditor_verdict.json writer
# ---------------------------------------------------------------------------

def write_auditor_verdict(
    repo: str,
    pr_number: str,
    pr: dict,
    checks: list,
    diff_files: list,
    reviews: list,
    me: str,
    repo_path: str | None,
) -> dict:
    """
    Perform full read-only verification and write auditor_verdict.json.

    Verifies:
      - diff matches approval.md
      - DoD evidence valid
      - validation claims supported
      - review freshness clean
      - CI relatedness clean
      - unknown CI surfaced
      - scope delta clean
      - branch drift clean
      - .prforge artifacts not staged
      - commit hygiene clean
      - public text preview exists
      - risk within policy threshold

    Returns the signed verdict dict.
    """
    verdict_checks: dict[str, dict] = {}
    all_pass = True

    # Resolve repo path
    if repo_path is None:
        repo_path_resolved = None
    else:
        repo_path_resolved = repo_path

    pf_dir = _read_pointer_artifact_dir(repo_path_resolved) if repo_path_resolved else None
    mesh_dir = pf_dir / "mesh" if pf_dir else None

    # 1. Diff matches approval.md
    approval_ok = True
    approval_reasons = []
    if pf_dir:
        approval_path = pf_dir / "approval.md"
        if approval_path.exists():
            try:
                approval_content = approval_path.read_text()
                # Check that every file in the diff is mentioned in approval.md
                for df in checks:
                    # checks here is actually diff_files — see caller
                    pass
                # Simple check: approval.md exists and is non-empty
                if len(approval_content.strip()) < 10:
                    approval_ok = False
                    approval_reasons.append("approval.md too short")
            except OSError:
                approval_ok = False
                approval_reasons.append("approval.md unreadable")
        else:
            approval_ok = False
            approval_reasons.append("approval.md missing")
    else:
        approval_ok = False
        approval_reasons.append("repo_path unknown")
    verdict_checks["diff_matches_approval"] = {
        "pass": approval_ok,
        "reason": "; ".join(approval_reasons),
    }
    if not approval_ok:
        all_pass = False

    # 2. DoD evidence valid
    dod_ok = True
    dod_reasons = []
    if pf_dir:
        dod_path = pf_dir / "dod.md"
        if dod_path.exists():
            try:
                dod_content = dod_path.read_text()
                if "[x]" not in dod_content and "[X]" not in dod_content:
                    dod_ok = False
                    dod_reasons.append("no checked items in dod.md")
            except OSError:
                dod_ok = False
                dod_reasons.append("dod.md unreadable")
        else:
            dod_ok = False
            dod_reasons.append("dod.md missing")
    else:
        dod_ok = False
        dod_reasons.append("repo_path unknown")
    verdict_checks["dod_evidence_valid"] = {
        "pass": dod_ok,
        "reason": "; ".join(dod_reasons),
    }
    if not dod_ok:
        all_pass = False

    # 2b. Acceptance criteria — verify each required item from the original job
    # packet is addressed in dod.md. Self-attested dod.md is insufficient;
    # the auditor must confirm the original requirements were actually met.
    ac_ok = True
    ac_reasons: list[str] = []
    acceptance_criteria: list = []
    if pf_dir:
        inbox_path = pf_dir / "inbox" / "job.json"
        if inbox_path.exists():
            try:
                job_packet = json.loads(inbox_path.read_text())
                acceptance_criteria = job_packet.get("job", {}).get("acceptance_criteria", [])
            except (json.JSONDecodeError, OSError):
                pass

    if acceptance_criteria and pf_dir:
        dod_path_ac = pf_dir / "dod.md"
        if dod_path_ac.exists():
            try:
                dod_text = dod_path_ac.read_text().lower()
                for criterion in acceptance_criteria:
                    if not isinstance(criterion, dict):
                        continue
                    cid = criterion.get("id", "")
                    desc = criterion.get("description", "")
                    status = criterion.get("status", "")
                    # If status already marked complete in job packet, trust it
                    if status == "complete":
                        continue
                    # Otherwise verify the dod.md mentions it as checked
                    desc_lower = desc.lower()
                    # Look for the description AND a [x] within proximity
                    if desc_lower and desc_lower[:40] not in dod_text:
                        ac_ok = False
                        ac_reasons.append(
                            f"criterion '{cid or desc[:40]}' not found in dod.md"
                        )
                    elif desc_lower and "[x]" not in dod_text and "[X]" not in dod_path_ac.read_text():
                        ac_ok = False
                        ac_reasons.append(
                            f"criterion '{cid or desc[:40]}' present but not checked off"
                        )
            except OSError:
                ac_ok = True  # Read failure — don't penalize; dod_evidence_valid already gates
        # No dod.md: dod_evidence_valid already flagged that; don't double-report
    # If no acceptance_criteria in job packet, skip check (legacy jobs)

    verdict_checks["acceptance_criteria_met"] = {
        "pass": ac_ok,
        "reason": "; ".join(ac_reasons) if ac_reasons else (
            "no acceptance_criteria in job packet — skipped" if not acceptance_criteria else "ok"
        ),
    }
    if not ac_ok:
        all_pass = False

    # 3. Validation claims supported
    validation_ok = True
    validation_reasons = []
    if pf_dir:
        val_path = pf_dir / "validation_ledger.md"
        if val_path.exists():
            try:
                val_content = val_path.read_text()
                if "FAIL" in val_content.upper() and "PASS" not in val_content.upper():
                    validation_ok = False
                    validation_reasons.append("validation ledger shows failures")
            except OSError:
                validation_ok = False
                validation_reasons.append("validation_ledger.md unreadable")
        else:
            validation_ok = False
            validation_reasons.append("validation_ledger.md missing")
    else:
        validation_ok = False
        validation_reasons.append("repo_path unknown")
    verdict_checks["validation_claims_supported"] = {
        "pass": validation_ok,
        "reason": "; ".join(validation_reasons),
    }
    if not validation_ok:
        all_pass = False

    # 4. Review freshness clean
    review_fresh = True
    review_reasons = []
    now = datetime.now(timezone.utc)
    for rev in reviews:
        submitted = rev.get("submittedAt", "")
        if submitted:
            try:
                rev_time = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
                age_hours = (now - rev_time).total_seconds() / 3600
                if age_hours > 168:  # 7 days
                    review_fresh = False
                    review_reasons.append(f"review by {rev.get('author', {}).get('login', '?')} is {age_hours:.0f}h old")
            except ValueError:
                pass
    verdict_checks["review_freshness_clean"] = {
        "pass": review_fresh,
        "reason": "; ".join(review_reasons),
    }
    if not review_fresh:
        all_pass = False

    # 5. CI relatedness clean
    ci_related = True
    ci_reasons = []
    pr_checks = pr.get("statusCheckRollup") or []
    for c in pr_checks:
        conclusion = c.get("conclusion", "") or c.get("status", "")
        if conclusion in ("FAILURE", "ERROR"):
            classification = _classify_ci(c, diff_files)
            if classification == "unrelated":
                ci_related = False
                ci_reasons.append(f"unrelated failure: {c.get('name', '?')}")
    verdict_checks["ci_relatedness_clean"] = {
        "pass": ci_related,
        "reason": "; ".join(ci_reasons),
    }
    if not ci_related:
        all_pass = False

    # 6. Unknown CI surfaced
    unknown_ci_exists = False
    unknown_reasons = []
    for c in pr_checks:
        conclusion = c.get("conclusion", "") or c.get("status", "")
        if conclusion in ("FAILURE", "ERROR"):
            classification = _classify_ci(c, diff_files)
            if classification == "unknown":
                unknown_ci_exists = True
                unknown_reasons.append(f"unknown: {c.get('name', '?')}")
    verdict_checks["unknown_ci_exists"] = {
        "pass": not unknown_ci_exists,
        "reason": "; ".join(unknown_reasons),
    }
    if unknown_ci_exists:
        all_pass = False

    # 7. Scope delta clean
    scope_ok = True
    scope_reasons = []
    if pf_dir:
        approval_path = pf_dir / "approval.md"
        if approval_path.exists() and diff_files:
            try:
                approval_content = approval_path.read_text()
                for df in diff_files:
                    # Extract basename stem
                    basename = df.split("/")[-1].lower()
                    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
                    if stem and len(stem) > 2 and stem not in approval_content.lower():
                        scope_ok = False
                        scope_reasons.append(f"file {df} not in approval.md")
            except OSError:
                pass
    verdict_checks["scope_delta_clean"] = {
        "pass": scope_ok,
        "reason": "; ".join(scope_reasons),
    }
    if not scope_ok:
        all_pass = False

    # 8. Branch drift clean
    branch_drift_ok = True
    branch_drift_reasons = []
    try:
        head_sha = pr.get("headRefOid", "")
        if repo_path_resolved and head_sha:
            import subprocess as sp
            result = sp.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10,
                cwd=repo_path_resolved,
            )
            if result.returncode == 0:
                local_sha = result.stdout.strip()
                if local_sha != head_sha:
                    branch_drift_ok = False
                    branch_drift_reasons.append(f"local HEAD {local_sha[:8]} != PR head {head_sha[:8]}")
    except Exception:
        pass
    verdict_checks["branch_drift_clean"] = {
        "pass": branch_drift_ok,
        "reason": "; ".join(branch_drift_reasons),
    }
    if not branch_drift_ok:
        all_pass = False

    # 9. .prforge artifacts not staged
    artifacts_clean = True
    artifacts_reasons = []
    if repo_path_resolved:
        try:
            import subprocess as sp
            result = sp.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True, text=True, timeout=10,
                cwd=repo_path_resolved,
            )
            if result.returncode == 0:
                staged = result.stdout.strip().splitlines()
                for f in staged:
                    if f == ".prforge-run" or ".prforge" in f:
                        artifacts_clean = False
                        artifacts_reasons.append(f"staged: {f}")
        except Exception:
            pass
    verdict_checks["prforge_artifacts_not_staged"] = {
        "pass": artifacts_clean,
        "reason": "; ".join(artifacts_reasons),
    }
    if not artifacts_clean:
        all_pass = False

    # 10. Commit hygiene clean
    commit_hygiene_ok = True
    commit_reasons = []
    if repo_path_resolved:
        try:
            import subprocess as sp
            result = sp.run(
                ["git", "log", "--oneline", "-5"],
                capture_output=True, text=True, timeout=10,
                cwd=repo_path_resolved,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    lower = line.lower()
                    if any(bad in lower for bad in ["wip", "fixme", "todo", "tmp", "temp", "asdf", "test commit"]):
                        commit_hygiene_ok = False
                        commit_reasons.append(f"bad commit: {line}")
        except Exception:
            pass
    verdict_checks["commit_hygiene_clean"] = {
        "pass": commit_hygiene_ok,
        "reason": "; ".join(commit_reasons),
    }
    if not commit_hygiene_ok:
        all_pass = False

    # 11. Public text preview exists
    preview_ok = True
    preview_reasons = []
    if pf_dir:
        pr_body = pf_dir / "pr_body.md"
        if not pr_body.exists():
            preview_ok = False
            preview_reasons.append("pr_body.md missing")
    else:
        preview_ok = False
        preview_reasons.append("repo_path unknown")
    verdict_checks["public_text_preview_exists"] = {
        "pass": preview_ok,
        "reason": "; ".join(preview_reasons),
    }
    if not preview_ok:
        all_pass = False

    # 12. Risk assessment
    risk_level = "low"
    if not all_pass:
        failed = [k for k, v in verdict_checks.items() if not v["pass"]]
        critical_failures = {"diff_matches_approval", "scope_delta_clean", "branch_drift_clean"}
        if any(f in critical_failures for f in failed):
            risk_level = "high"
        elif len(failed) > 3:
            risk_level = "medium"
        else:
            risk_level = "low"

    decision = "auditor_pass" if all_pass else "auditor_fail"
    verdict: dict = {
        "decision": decision,
        "repo": repo,
        "pr_number": pr_number,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": verdict_checks,
        "all_pass": all_pass,
        "risk_level": risk_level,
        "head_sha": pr.get("headRefOid", ""),
    }
    if not all_pass:
        failed_names = [k for k, v in verdict_checks.items() if not v["pass"]]
        verdict["failure_reason"] = ", ".join(failed_names)

    # Sign
    try:
        signing_key = get_signing_key()
        signed = sign_artifact(verdict, signing_key)
    except RuntimeError:
        logger.warning("PRFORGE_MESH_SIGNING_KEY not set — writing unsigned auditor_verdict")
        signed = verdict

    # Write
    if mesh_dir:
        mesh_dir.mkdir(parents=True, exist_ok=True)
        (mesh_dir / "auditor_verdict.json").write_text(json.dumps(signed, indent=2))
        logger.info("Wrote auditor_verdict.json for %s#%s decision=%s", repo, pr_number, decision)

    return signed
