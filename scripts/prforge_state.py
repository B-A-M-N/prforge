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
from pathlib import Path


def lock_file_for(path: str) -> str:
    """Return the lock file path for a given state file."""
    return path + ".lock"


def read_state(state_file: str, timeout: float = 10.0) -> dict:
    """Read state.json with a shared (read) lock. Returns parsed JSON or empty dict."""
    if not os.path.exists(state_file):
        return {}
    lock_path = lock_file_for(state_file)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDONLY, 0o644)
    try:
        # Shared lock (read lock) — multiple readers allowed
        fcntl.flock(lock_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        # If LOCK_NB fails, wait with a timeout
        try:
            with open(state_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(lock_fd)


def write_state(state_file: str, data: dict, timeout: float = 10.0) -> bool:
    """Write state.json atomically with an exclusive lock. Returns True on success."""
    lock_path = lock_file_for(state_file)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        # Exclusive lock with timeout
        import time
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (IOError, OSError):
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.05)

        # Atomic write: write to temp, then rename
        dir_name = os.path.dirname(state_file) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp", prefix=".state_")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp_path, state_file)
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
    ok = write_state(state_file, data)
    if not ok:
        print("ERROR: Failed to acquire lock for write", file=sys.stderr)
        sys.exit(1)
    print(f"OK: wrote {state_file}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "read":
        cli_read(sys.argv[2:])
    elif cmd == "write":
        cli_write(sys.argv[2:])
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print("Usage: prforge_state.py read|write ...", file=sys.stderr)
        sys.exit(1)
