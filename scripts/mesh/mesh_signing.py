"""
PRForge Mesh — canonical JSON + HMAC-SHA256 signing.

All verdict and certification artifacts are signed so that:
  - Tampering is detectable (signature mismatch)
  - Provenance is verifiable (only nodes with PRFORGE_MESH_SIGNING_KEY can sign)
  - No Claude-generated prose signatures are used — only HMAC-SHA256 hex digests

Usage:
    sign_artifact(data: dict, key: str) -> dict
    verify_artifact(signed: dict, key: str) -> bool
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any


SIGNATURE_FIELD = "_signature"
SIGNING_KEY_ENV = "PRFORGE_MESH_SIGNING_KEY"


def get_signing_key() -> str:
    """Return the signing key from environment. Raises if missing."""
    key = os.environ.get(SIGNING_KEY_ENV, "")
    if not key:
        raise RuntimeError(
            f"{SIGNING_KEY_ENV} not set. "
            "Export it before running mesh daemons: "
            f"export {SIGNING_KEY_ENV}=<your-secret>"
        )
    return key


def _canonical_json(data: dict) -> str:
    """
    Produce a deterministic, canonical JSON representation.
    - Keys sorted lexicographically at every level
    - No extra whitespace
    - No _signature field (signing must not be self-referential)
    """
    # Strip signature if present before serializing
    stripped = {k: v for k, v in data.items() if k != SIGNATURE_FIELD}
    return json.dumps(stripped, sort_keys=True, separators=(",", ":"), default=_coerce)


def _coerce(obj: Any) -> Any:
    """Coerce non-serializable types to strings for canonical form."""
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_coerce(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _coerce(v) for k, v in obj.items()}
    return str(obj)


def sign_artifact(data: dict, key: str | None = None) -> dict:
    """
    Return a new dict with an added _signature field.
    The signature is HMAC-SHA256(canonical_json(data), key).
    Original data is not mutated.
    """
    if key is None:
        key = get_signing_key()
    canonical = _canonical_json(data)
    sig = hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {**data, SIGNATURE_FIELD: sig}


def verify_artifact(data: dict, key: str | None = None) -> bool:
    """
    Verify the _signature field matches the canonical JSON of data.
    Returns True if valid, False if tampered or missing signature.
    """
    if SIGNATURE_FIELD not in data:
        return False
    if key is None:
        try:
            key = get_signing_key()
        except RuntimeError:
            return False
    expected_sig = data[SIGNATURE_FIELD]
    canonical = _canonical_json(data)
    actual_sig = hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_sig, actual_sig)
