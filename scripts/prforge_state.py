#!/usr/bin/env python3
"""PRForge state.json locked read/write helper.

Usage:
  # Read state (shared lock):
  python3 prforge_state.py read /path/to/state.json

  # Write state (exclusive lock + atomic write):
  python3 prforge_state.py write /path/to/state.json /path/to/temp.json

  # From inline Python in hooks, import directly:
  # from prforge_state import read_state, write_state
"""

import json
import fcntl
import os
import sys
import tempfile
import time
from pathlib import Path


def lock_file_for(path: str) -> str:
    """Return the lock file path for a given state file."""
    return path + ".lock"


class StateError(Exception):
    """State read/write/validation error."""


def _acquire_flock(fd: int, lock_type: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, lock_type | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)


def read_state_strict(state_file: str, timeout: float = 10.0) -> dict:
    """Read state.json with a shared lock. Raises StateError on timeout/corruption."""
    if not os.path.exists(state_file):
        return {}
    lock_path = lock_file_for(state_file)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDONLY, 0o644)
    try:
        if not _acquire_flock(lock_fd, fcntl.LOCK_SH, timeout):
            raise StateError(f"Timed out acquiring read lock for {state_file}")
        try:
            with open(state_file, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            raise StateError(f"Invalid JSON in {state_file}: {exc}") from exc
        except OSError as exc:
            raise StateError(f"Could not read {state_file}: {exc}") from exc
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(lock_fd)


def read_state(state_file: str, timeout: float = 10.0) -> dict:
    """Compatibility read: returns parsed JSON or empty dict on missing/corrupt state."""
    try:
        return read_state_strict(state_file, timeout=timeout)
    except StateError:
        return {}


def default_schema_path() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "state-schema.json"


def _type_matches(value, expected) -> bool:
    if isinstance(expected, list):
        return any(_type_matches(value, e) for e in expected)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate_subset(value, schema: dict, path: str, errors: list[str]) -> None:
    if "type" in schema and not _type_matches(value, schema["type"]):
        errors.append(f"{path}: expected type {schema['type']}")
        return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value {value!r} not in enum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required field missing")
        props = schema.get("properties", {})
        for key, child in value.items():
            if key in props and isinstance(props[key], dict):
                _validate_subset(child, props[key], f"{path}.{key}", errors)
    elif isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                _validate_subset(item, item_schema, f"{path}[{i}]", errors)


def validate_state(data: dict, schema_file: str | None = None) -> list[str]:
    """Validate state against the checked-in JSON schema subset used by PRForge."""
    schema_path = Path(schema_file) if schema_file else default_schema_path()
    if not schema_path.exists():
        return [f"schema file missing: {schema_path}"]
    try:
        schema = json.loads(schema_path.read_text())
    except Exception as exc:
        return [f"schema unreadable: {exc}"]
    errors: list[str] = []
    _validate_subset(data, schema, "$", errors)
    return errors


def migrate_state(data: dict) -> dict:
    """Return a schema-compatible state object from older/partial producers."""
    if not isinstance(data, dict):
        data = {}
    migrated = dict(data)
    migrated.setdefault("version", "1.0")
    phase = migrated.get("phase") or "BLOCKED"
    if phase in ("CONTRACT", "REPRODUCE"):
        phase = "INVESTIGATE"
    elif phase in ("SHIPPED", "SHIPPED_PENDING"):
        phase = "POSTMORTEM"
        migrated.setdefault("approval", {})["consumed"] = True
    migrated["phase"] = phase

    repo = migrated.get("repo")
    if not isinstance(repo, dict):
        repo = {}
    repo.setdefault("local_path", "")
    repo.setdefault("base_branch", "")
    repo.setdefault("working_branch", repo.get("branch", ""))
    migrated["repo"] = repo

    task = migrated.get("task")
    if not isinstance(task, dict):
        task = {}
    task.setdefault("type", "local_task")
    if task.get("type") not in {
        "new_pr", "review_response", "issue_fix", "local_task",
        "ci_fix", "candidate_discovery", "pr_polish",
    }:
        task["type"] = "local_task"
    task.setdefault("objective", migrated.get("objective") or "Unspecified PRForge task")
    migrated["task"] = task

    permissions = migrated.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
    migrated["permissions"] = permissions
    migrated.setdefault("started_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    blocker = migrated.get("blocker")
    if isinstance(blocker, str):
        migrated["blocker"] = {
            "reason": "legacy_blocker",
            "details": blocker,
            "suggested_fix": "Resolve the blocker and update state with structured blocker fields.",
        }
    return migrated


def write_state(state_file: str, data: dict, timeout: float = 10.0, schema_file: str | None = None) -> bool:
    """Write state.json atomically with an exclusive lock. Returns True on success."""
    data = migrate_state(data)
    errors = validate_state(data, schema_file=schema_file)
    if errors:
        raise StateError("State schema validation failed: " + "; ".join(errors[:10]))
    lock_path = lock_file_for(state_file)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if not _acquire_flock(lock_fd, fcntl.LOCK_EX, timeout):
            return False

        # Atomic write: write to temp, then rename
        dir_name = os.path.dirname(state_file) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp", prefix=".state_")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, state_file)
            dir_fd = os.open(dir_name, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
        return True
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(lock_fd)
        except Exception:
            pass


def cli_read(args):
    state_file = args[0] if args else ""
    if not state_file:
        print("Usage: prforge_state.py read <state.json>", file=sys.stderr)
        sys.exit(1)
    strict = "--strict" in args
    if strict:
        data = read_state_strict(state_file)
    else:
        data = read_state(state_file)
    print(json.dumps(data, indent=2))


def cli_write(args):
    state_file = args[0] if len(args) > 0 else ""
    json_file = args[1] if len(args) > 1 else ""
    if not state_file or not json_file:
        print("Usage: prforge_state.py write <state.json> <data.json>", file=sys.stderr)
        sys.exit(1)
    with open(json_file, "r") as f:
        data = json.load(f)
    try:
        ok = write_state(state_file, data)
        if not ok:
            print("ERROR: Failed to acquire lock for write", file=sys.stderr)
            sys.exit(1)
    except StateError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: wrote {state_file}")


def cli_validate(args):
    state_file = args[0] if args else ""
    if not state_file:
        print("Usage: prforge_state.py validate <state.json>", file=sys.stderr)
        sys.exit(1)
    try:
        data = read_state_strict(state_file)
    except StateError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    errors = validate_state(data)
    if errors:
        print("FAIL: " + " | ".join(errors), file=sys.stderr)
        sys.exit(1)
    print("OK")


def cli_migrate(args):
    state_file = args[0] if args else ""
    if not state_file:
        print("Usage: prforge_state.py migrate <state.json>", file=sys.stderr)
        sys.exit(1)
    try:
        data = read_state_strict(state_file)
    except StateError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    migrated = migrate_state(data)
    errors = validate_state(migrated)
    if errors:
        print("FAIL: " + " | ".join(errors), file=sys.stderr)
        sys.exit(1)
    if migrated != data:
        if not write_state(state_file, migrated):
            print("ERROR: Failed to acquire lock for write", file=sys.stderr)
            sys.exit(1)
        print("MIGRATED")
    else:
        print("OK")


def cli_recover(args):
    state_file = args[0] if args else ""
    if not state_file:
        print("Usage: prforge_state.py recover <state.json>", file=sys.stderr)
        sys.exit(1)
    path = Path(state_file)
    if not path.exists():
        print("ERROR: state file not found", file=sys.stderr)
        sys.exit(1)
    try:
        read_state_strict(str(path))
        print("OK: state is readable; no recovery needed")
        return
    except StateError:
        pass
    backup = path.with_suffix(path.suffix + f".corrupt-{int(time.time())}")
    os.replace(path, backup)
    recovered = {
        "version": "1.0",
        "phase": "BLOCKED",
        "repo": {"local_path": "", "base_branch": "", "working_branch": ""},
        "task": {
            "type": "local_task",
            "objective": "Recover PRForge state from corrupt JSON backup",
        },
        "permissions": {},
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "blocker": {
            "reason": "state_json_corrupt",
            "details": f"Recovered from corrupt state backup: {backup.name}",
            "suggested_fix": "Inspect the backup, rebuild missing run metadata, then resume from a valid phase.",
        },
    }
    write_state(str(path), recovered)
    print(f"RECOVERED: moved corrupt state to {backup}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "read":
        cli_read(sys.argv[2:])
    elif cmd == "write":
        cli_write(sys.argv[2:])
    elif cmd == "validate":
        cli_validate(sys.argv[2:])
    elif cmd == "migrate":
        cli_migrate(sys.argv[2:])
    elif cmd == "recover":
        cli_recover(sys.argv[2:])
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print("Usage: prforge_state.py read|write|validate|migrate|recover ...", file=sys.stderr)
        sys.exit(1)
