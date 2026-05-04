"""
PRForge Mesh — notifications.
Desktop (notify-send) + Redis Pub/Sub publish.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Optional

import redis as redis_lib


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

EVENTS = {
    "ReviewDetected",
    "AuditFindingCreated",
    "JobQueued",
    "JobDispatched",
    "JobRequeued",
    "JobBlocked",
    "WorkerBlocked",
    "ApprovalReady",
    "WorkerSubmissionReady",
    "CoordinatorVerdictWritten",
    "AuditorVerdictWritten",
    "ManagerVerdictWritten",
    "ManagerCertified",
    "ManagerRequeued",
    "ManagerBlocked",
    "ManagerEscalated",
    "LeaseExpired",
    "WorkerOffline",
    "WorkerIdle",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def notify(
    r: Optional[redis_lib.Redis],
    cluster: str,
    event: str,
    message: str,
    *,
    desktop: bool = True,
    pubsub: bool = True,
) -> None:
    if desktop:
        _desktop(event, message)
    if pubsub and r is not None:
        _pubsub(r, cluster, event, message)


def _desktop(event: str, message: str) -> None:
    if shutil.which("notify-send") is None:
        return
    try:
        subprocess.run(
            ["notify-send", "PRForge Mesh", f"[{event}] {message}"],
            timeout=3,
            capture_output=True,
        )
    except Exception:
        pass


def _pubsub(r: redis_lib.Redis, cluster: str, event: str, message: str) -> None:
    channel = f"Workflow:{cluster}:notify"
    payload = json.dumps({"event": event, "message": message})
    try:
        r.publish(channel, payload)
    except Exception:
        pass
