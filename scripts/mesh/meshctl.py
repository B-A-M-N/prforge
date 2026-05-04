#!/usr/bin/env python3
"""
PRForge Mesh Controller (meshctl)
Unified internal controller for distributed mesh mode.

Handles: config writing, Redis management, service lifecycle,
heartbeat, health checks, auto-heal, session IDs, join secrets.
"""

import argparse
import json
import os
import platform
import random
import secrets
import socket
import string
import subprocess
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRFORGE_DIR = Path.home() / ".prforge-mesh"
LOCAL_DIR = PRFORGE_DIR / "local"
LAN_DIR = PRFORGE_DIR / "lan"
SESSIONS_DIR = PRFORGE_DIR / "sessions"
LOCK_FILE = LOCAL_DIR / ".assign.lock"
REDIS_LOCAL_PORT_DEFAULT = 6385
REDIS_LAN_PORT_DEFAULT = 6386


# ---------------------------------------------------------------------------
# Session ID
# ---------------------------------------------------------------------------

def get_session_id() -> str:
    """Return a stable session ID for this Claude Code instance."""
    # Check if Claude exposes a session ID via env
    for env_var in ("CLAUDE_SESSION_ID", "PRFORGE_SESSION_ID"):
        val = os.environ.get(env_var)
        if val:
            return val

    # Generate + persist a PRForge session ID
    session_file = PRFORGE_DIR / ".session_id"
    if session_file.exists():
        sid = session_file.read_text().strip()
        if sid:
            return sid

    sid = str(uuid.uuid4())
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(sid)
    os.environ["PRFORGE_SESSION_ID"] = sid
    return sid


# ---------------------------------------------------------------------------
# Session Pointer Helpers
# ---------------------------------------------------------------------------

def get_session_pointer_path(mode: str, session_id: str = None) -> Path:
    """Get the path to this session's node pointer."""
    sid = session_id or get_session_id()
    return SESSIONS_DIR / mode / sid


def read_session_pointer(mode: str, session_id: str = None) -> str | None:
    """Read which node this session is assigned to."""
    ptr = get_session_pointer_path(mode, session_id)
    if ptr.exists():
        return ptr.read_text().strip() or None
    return None


def write_session_pointer(mode: str, node_id: str, session_id: str = None) -> None:
    """Write that this session is assigned to a node."""
    ptr = get_session_pointer_path(mode, session_id)
    ptr.parent.mkdir(parents=True, exist_ok=True)
    ptr.write_text(node_id)


# ---------------------------------------------------------------------------
# Lock File Helpers
# ---------------------------------------------------------------------------

def acquire_lock(lock_path: Path, timeout: int = 10) -> bool:
    """Acquire a lock file. Returns True if acquired."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{get_session_id()}\n".encode())
            os.close(fd)
            return True
        except FileExistsError:
            time.sleep(0.2)
    return False


def release_lock(lock_path: Path) -> None:
    """Release a lock file."""
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Port Detection
# ---------------------------------------------------------------------------

def find_available_port(start_port: int, attempts: int = 5) -> int:
    """Find an available port starting from start_port."""
    env_override = os.environ.get("PRFORGE_REDIS_PORT")
    if env_override:
        return int(env_override)

    for i in range(attempts):
        port = start_port + i
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", port))
            s.close()
            return port
        except OSError:
            continue
    return start_port  # fallback


def get_redis_port(mode: str, config: dict = None) -> int:
    """Get the Redis port for a mode, reading from config if available."""
    env_override = os.environ.get("PRFORGE_REDIS_PORT")
    if env_override:
        return int(env_override)

    if config:
        port = config.get("redis_port")
        if port:
            return int(port)

    if mode == "local":
        return find_available_port(REDIS_LOCAL_PORT_DEFAULT)
    else:
        return find_available_port(REDIS_LAN_PORT_DEFAULT)


# ---------------------------------------------------------------------------
# Redis Config Generation
# ---------------------------------------------------------------------------

def generate_redis_config(mode: str, port: int, secret: str = None) -> Path:
    """Generate PRForge Redis config. Returns path to config file."""
    if mode == "local":
        conf_dir = PRFORGE_DIR / "redis"
        conf_path = conf_dir / "redis-local.conf"
        bind = "127.0.0.1"
    else:
        conf_dir = PRFORGE_DIR / "redis"
        conf_path = conf_dir / "redis-lan.conf"
        bind = "0.0.0.0"

    conf_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        f"port {port}",
        f"bind {bind}",
        "appendonly yes",
        "daemonize yes",
        f"pidfile {conf_dir}/redis-{mode}.pid",
        f"logfile {conf_dir}/redis-{mode}.log",
        f"dir {conf_dir}",
        "maxmemory 256mb",
        "maxmemory-policy allkeys-lru",
    ]

    if mode == "lan" and secret:
        lines.append(f"requirepass {secret}")
        lines.append(f"masterauth {secret}")

    conf_path.write_text("\n".join(lines) + "\n")
    return conf_path


# ---------------------------------------------------------------------------
# Redis Management
# ---------------------------------------------------------------------------

def redis_ping(host: str = "127.0.0.1", port: int = REDIS_LOCAL_PORT_DEFAULT,
             password: str = None, timeout: int = 5) -> bool:
    """Check if Redis is reachable."""
    try:
        import redis
        r = redis.Redis(host=host, port=port, password=password,
                        socket_timeout=timeout, decode_responses=True)
        return r.ping()
    except Exception:
        return False


def start_redis(config_path: Path) -> bool:
    """Start PRForge Redis using its config."""
    try:
        result = subprocess.run(
            ["redis-server", str(config_path)],
            capture_output=True, timeout=10
        )
        # Give it a moment to start
        time.sleep(1)
        return redis_ping()
    except Exception as e:
        print(f"Redis start failed: {e}", file=sys.stderr)
        return False


def stop_redis(mode: str) -> bool:
    """Stop PRForge Redis."""
    pid_file = PRFORGE_DIR / "redis" / f"redis-{mode}.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 15)  # SIGTERM
            return True
        except Exception:
            pass
    return False


def ensure_redis(mode: str, port: int, secret: str = None) -> bool:
    """Ensure PRForge Redis is running. Start if not."""
    # Check if already running
    host = "127.0.0.1" if mode == "local" else "0.0.0.0"
    if redis_ping(host=host if mode == "local" else "127.0.0.1", port=port):
        return True

    # Generate config and start
    config_path = generate_redis_config(mode, port, secret)
    return start_redis(config_path)


# ---------------------------------------------------------------------------
# Node Config
# ---------------------------------------------------------------------------

def get_node_dir(mode: str, node_id: str) -> Path:
    """Get the config directory for a node."""
    if mode == "local":
        return LOCAL_DIR / node_id
    else:
        return LAN_DIR / node_id


def write_node_config(mode: str, node_id: str, role: str, redis_url: str,
                       extra: dict = None) -> Path:
    """Write node config.json. Returns config path."""
    node_dir = get_node_dir(mode, node_id)
    node_dir.mkdir(parents=True, exist_ok=True)
    config_path = node_dir / "config.json"

    config = {
        "mesh": {
            "enabled": True,
            "redis_url": redis_url,
            "cluster_name": "default",
            "node_id": node_id,
            "roles": [role] if role == "forge" else ["coordinator", "auditor"],
        },
        "limits": {
            "max_active_worker_jobs": 2,
            "max_jobs_per_worker": 1,
            "lease_ttl_seconds": 1800,
            "heartbeat_interval_seconds": 15,
        },
        "worker": {
            "capacity": 1,
            "repo_roots": extra.get("repo_roots", []) if extra else [],
            "auto_launch_claude": False,
            "launcher": "openclaude1",
            "allowed_modes": ["new_pr", "review_response", "pr_polish", "ci_fix_related_to_branch"]
        },
        "auditor": {
            "enabled": role != "forge",
            "lookback_days": 3,
            "poll_interval_minutes": 15,
            "audit_interval_minutes": 45,
            "skip_if_unchanged": True,
            "max_llm_audits_per_hour": 3,
            "queue_medium_findings_only_when_idle": True,
        },
        "notifications": {"desktop": True, "pubsub": True},
        "manager_mode": {
            "enabled": role != "forge",
            "authority": "off" if role == "forge" else "internal_actions",
        },
    }

    # Add redis_port for reference
    if extra and "redis_port" in extra:
        config["mesh"]["redis_port"] = extra["redis_port"]

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    return config_path


# ---------------------------------------------------------------------------
# Join Secret
# ---------------------------------------------------------------------------

def generate_join_secret() -> str:
    """Generate a human-readable join code like '7KQ4-MESH'."""
    parts = []
    for _ in range(2):
        part = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        parts.append(part)
    return "-".join(parts)


def get_or_create_secret(watchtower_dir: Path) -> str:
    """Get existing secret or create one."""
    secret_file = watchtower_dir / "mesh-secret"
    if secret_file.exists():
        return secret_file.read_text().strip()
    secret = generate_join_secret()
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(secret)
    return secret


# ---------------------------------------------------------------------------
# Forge Assignment
# ---------------------------------------------------------------------------

def get_next_forge_id(mode: str, lock: bool = True) -> str | None:
    """Get the next available forge-N ID. Uses lock if requested."""
    acquired = False
    try:
        if lock:
            acquired = acquire_lock(LOCK_FILE)
            if not acquired:
                return None

        base_dir = LOCAL_DIR if mode == "local" else LAN_DIR
        existing = []
        if base_dir.exists():
            for d in base_dir.iterdir():
                if d.is_dir() and d.name.startswith("forge-"):
                    n = d.name.split("-", 1)[1]
                    if n.isdigit():
                        existing.append(int(n))

        next_n = 1
        if existing:
            next_n = max(existing) + 1
        return f"forge-{next_n}"
    finally:
        if acquired and lock:
            release_lock(LOCK_FILE)


# ---------------------------------------------------------------------------
# Service Management
# ---------------------------------------------------------------------------

def get_service_name(mode: str, node_id: str) -> str:
    """Get systemd service name for a node."""
    prefix = "prforge-local" if mode == "local" else "prforge-lan"
    return f"{prefix}-{node_id}.service"


def generate_service_file(mode: str, node_id: str, redis_url: str,
                       mesh_scripts: Path) -> Path:
    """Generate systemd service file. Returns path to service file."""
    service_name = get_service_name(mode, node_id)
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / service_name

    env_file = PRFORGE_DIR / "mesh.env"
    if mode == "local":
        node_type = "watchtower" if node_id == "watchtower" else "worker"
    else:
        node_type = "coordinator+auditor" if node_id == "watchtower" else "worker"

    # Determine the command
    if node_id == "watchtower":
        cmd = "coordinator"
    else:
        cmd = "worker"

    node_dir = get_node_dir(mode, node_id)
    config_path = node_dir / "config.json"

    content = f"""\
[Unit]
Description=PRForge Mesh {node_id} ({mode})
After=network-online.target

[Service]
Type=simple
EnvironmentFile={env_file}
Environment=PRFORGE_MESH_CONFIG={config_path}
WorkingDirectory={mesh_scripts}
ExecStart=/usr/bin/python3 {mesh_scripts}/prforge_mesh.py --config {config_path} {cmd}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""
    service_path.write_text(content)
    return service_path


def start_service(mode: str, node_id: str) -> bool:
    """Start the systemd service for a node."""
    service_name = get_service_name(mode, node_id)
    try:
        # Reload first
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                    capture_output=True, timeout=10)
        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", service_name],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True
        print(f"Service start failed: {result.stderr}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Service start error: {e}", file=sys.stderr)
        return False


def stop_service(mode: str, node_id: str) -> bool:
    """Stop the systemd service for a node."""
    service_name = get_service_name(mode, node_id)
    try:
        result = subprocess.run(
            ["systemctl", "--user", "stop", service_name],
            capture_output=True, timeout=10
        )
        return True
    except Exception as e:
        print(f"Service stop error: {e}", file=sys.stderr)
        return False


def status_check(mode: str, session_id: str = None) -> dict:
    """Check mesh health. Returns status dict."""
    node_id = read_session_pointer(mode, session_id)
    if not node_id:
        return {"status": "no_session", "node_id": None}

    config_path = get_node_dir(mode, node_id) / "config.json"
    if not config_path.exists():
        return {"status": "no_config", "node_id": node_id}

    with open(config_path) as f:
        config = json.load(f)

    redis_url = config["mesh"]["redis_url"]
    # Parse Redis URL
    import re
    m = re.match(r"redis://(?:.*:)?([^:]+):(\d+)", redis_url)
    if m:
        host, port = m.group(1), int(m.group(2))
    else:
        host, port = "127.0.0.1", REDIS_LOCAL_PORT_DEFAULT

    secret = None
    if mode == "lan":
        secret_file = (LAN_DIR / "watchtower" / "mesh-secret")
        if secret_file.exists():
            secret = secret_file.read_text().strip()

    redis_ok = redis_ping(host=host, port=port, password=secret)

    # Check service
    service_name = get_service_name(mode, node_id)
    service_active = False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", service_name],
            capture_output=True, text=True, timeout=5
        )
        service_active = result.returncode == 0
    except Exception:
        pass

    return {
        "status": "healthy" if redis_ok and service_active else "unhealthy",
        "node_id": node_id,
        "redis_ok": redis_ok,
        "service_active": service_active,
        "config_path": str(config_path),
    }


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def cmd_health(args):
    """Run health check."""
    mode = args.mode
    session_id = args.session or get_session_id()
    status = status_check(mode, session_id)

    if status["status"] == "no_session":
        print("No active session found for this Claude instance.")
        sys.exit(1)

    if status["status"] == "healthy":
        print(f"✓ {status['node_id']} healthy — Redis OK, service active")
    else:
        print(f"✗ {status['node_id']} unhealthy — Redis: {status['redis_ok']}, Service: {status['service_active']}")
        sys.exit(1)


def cmd_heal(args):
    """Try to heal the mesh."""
    mode = args.mode
    session_id = args.session or get_session_id()
    status = status_check(mode, session_id)

    if status["status"] == "no_session":
        print("No active session found.")
        sys.exit(1)

    if status["status"] == "healthy":
        print(f"✓ {status['node_id']} already healthy")
        return

    print(f"Mesh was stale. Restarting {status['node_id']}...")
    # Try to restart the service
    if not status.get("service_active"):
        start_service(mode, status["node_id"])
        print(f"  Service restarted.")

    print("Continuing.")


def cmd_setup(args):
    """Setup a node (watchtower or forge)."""
    mode = args.mode
    role = args.role
    session_id = get_session_id()

    if role == "watchtower":
        node_id = "watchtower"
        # Generate secret for LAN mode
        secret = None
        if mode == "lan":
            secret = get_or_create_secret(LAN_DIR / "watchtower")
            print(f"Join code: {secret}")

        # Redis setup
        port = get_redis_port(mode)
        redis_url = f"redis://127.0.0.1:{port}/0"
        if mode == "lan":
            redis_url = f"redis://:{secret}@0.0.0.0:{port}/0"

        ensure_redis(mode, port, secret)

        # Write config
        write_node_config(mode, node_id, "watchtower", redis_url,
                        {"redis_port": port})

        # Generate + start service
        mesh_scripts = Path(__file__).parent
        generate_service_file(mode, node_id, redis_url, mesh_scripts)
        start_service(mode, node_id)

        # Write session pointer
        write_session_pointer(mode, node_id, session_id)

        print(f"✓ watchtower online — managing + auditing")
        if mode == "lan":
            # Get local IP
            local_ip = "127.0.0.1"
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                pass
            print(f"  Join forges with:")
            print(f"    Host: {local_ip}")
            print(f"    Code: {secret}")

    else:  # forge
        if mode == "local":
            acquire_lock(LOCK_FILE)
            try:
                node_id = get_next_forge_id(mode, lock=False)
            finally:
                release_lock(LOCK_FILE)

            port = get_redis_port(mode)
            redis_url = f"redis://127.0.0.1:{port}/0"

            # Check if watchtower is visible
            if not redis_ping(port=port):
                print(f"✓ {node_id} online — waiting for local watchtower")
            else:
                print(f"✓ {node_id} online — connected to local watchtower")
        else:  # LAN forge
            # Get watchtower host + secret
            host = args.host
            secret = args.code
            if not host or not secret:
                # Try to read saved
                host_file = LAN_DIR / "watchtower-host"
                secret_file = LAN_DIR / "watchtower-secret"
                if host_file.exists() and secret_file.exists():
                    host = host or host_file.read_text().strip()
                    secret = secret or secret_file.read_text().strip()
                else:
                    print("Watchtower hostname or IP required.", file=sys.stderr)
                    print("Usage: meshctl setup --mode lan --role forge --host HOST --code CODE", file=sys.stderr)
                    sys.exit(1)

            # Save for next time
            (LAN_DIR / "watchtower-host").write_text(host)
            (LAN_DIR / "watchtower-secret").write_text(secret)

            port = get_redis_port("lan")
            redis_url = f"redis://:{secret}@{host}:{port}/0"

            if not redis_ping(host=host, port=port, password=secret):
                print(f"✓ forge online — waiting for watchtower at {host}")
            else:
                print(f"✓ forge online — connected to watchtower at {host}")

            node_id = f"forge-{platform.node()}"

        # Write config + start service
        write_node_config(mode, node_id, "forge", redis_url,
                        {"redis_port": port})
        mesh_scripts = Path(__file__).parent
        generate_service_file(mode, node_id, redis_url, mesh_scripts)
        start_service(mode, node_id)
        write_session_pointer(mode, node_id, session_id)


def cmd_status(args):
    """Show mesh status."""
    mode = args.mode
    base_dir = LOCAL_DIR if mode == "local" else LAN_DIR

    print(f"PRForge {'Local' if mode == 'local' else 'LAN'} Mesh")
    print()

    if not base_dir.exists():
        print("  No nodes configured.")
        return

    for node_dir in sorted(base_dir.iterdir()):
        if not node_dir.is_dir():
            continue
        config_path = node_dir / "config.json"
        if not config_path.exists():
            continue

        with open(config_path) as f:
            config = json.load(f)

        node_id = config["mesh"]["node_id"]
        roles = config["mesh"]["roles"]

        # Check if service is running
        service_name = get_service_name(mode, node_id)
        active = "unknown"
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", service_name],
                capture_output=True, text=True, timeout=5
            )
            active = "online" if result.returncode == 0 else "offline"
        except Exception:
            pass

        role_str = "managing + auditing" if "coordinator" in roles else "idle"
        print(f"  {node_id:<15} {active:<10} {role_str}")


def cmd_stop(args):
    """Stop a node."""
    mode = args.mode
    session_id = get_session_id()
    node_id = read_session_pointer(mode, session_id)

    if not node_id:
        print("No active session found for this Claude instance.", file=sys.stderr)
        sys.exit(1)

    stop_service(mode, node_id)
    print(f"✓ {node_id} stopped")


def main():
    parser = argparse.ArgumentParser(description="PRForge Mesh Controller")
    subparsers = parser.add_subparsers(dest="command")

    # health
    p_health = subparsers.add_parser("health", help="Check mesh health")
    p_health.add_argument("--mode", choices=["local", "lan"], default="local")
    p_health.add_argument("--session", help="Session ID (auto-detected if omitted)")

    # heal
    p_heal = subparsers.add_parser("heal", help="Try to fix mesh issues")
    p_heal.add_argument("--mode", choices=["local", "lan"], default="local")
    p_heal.add_argument("--session", help="Session ID (auto-detected if omitted)")

    # setup
    p_setup = subparsers.add_parser("setup", help="Setup a node")
    p_setup.add_argument("--mode", choices=["local", "lan"], default="local")
    p_setup.add_argument("--role", choices=["watchtower", "forge"], required=True)
    p_setup.add_argument("--host", help="Watchtower host (LAN forge only)")
    p_setup.add_argument("--code", help="Join code (LAN forge only)")

    # status
    p_status = subparsers.add_parser("status", help="Show mesh status")
    p_status.add_argument("--mode", choices=["local", "lan"], default="local")

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop current node")
    p_stop.add_argument("--mode", choices=["local", "lan"], default="local")

    args = parser.parse_args()

    if args.command == "health":
        cmd_health(args)
    elif args.command == "heal":
        cmd_heal(args)
    elif args.command == "setup":
        cmd_setup(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "stop":
        cmd_stop(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
