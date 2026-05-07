#!/usr/bin/env python3
"""Redis-backed mesh integration tests using an in-memory Redis double.

These tests exercise PRForge's lease semantics without requiring a daemon. They
cover the failure modes that matter for coordinator/worker correctness:
duplicate job assignment, lease expiry, worker crash cleanup, path lock renewal,
and Redis outage behavior.
"""

from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "mesh"))

if "redis" not in sys.modules:
    fake_redis_module = types.ModuleType("redis")

    class _RedisType:  # minimal class for type annotations
        @classmethod
        def from_url(cls, *_args, **_kwargs):
            raise RedisDown("redis unavailable")

    fake_redis_module.Redis = _RedisType
    sys.modules["redis"] = fake_redis_module

import redis_backend as rb  # noqa: E402
import coordinator  # noqa: E402


class RedisDown(Exception):
    pass


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float | None]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise RedisDown("redis unavailable")
        now = time.time()
        for key, (_value, expires) in list(self.store.items()):
            if expires is not None and expires <= now:
                self.store.pop(key, None)

    def set(self, key, value, nx=False, ex=None):
        self._check()
        if nx and key in self.store:
            return None
        self.store[key] = (value, time.time() + ex if ex else None)
        return True

    def get(self, key):
        self._check()
        entry = self.store.get(key)
        return entry[0] if entry else None

    def delete(self, key):
        self._check()
        self.store.pop(key, None)
        self.hashes.pop(key, None)
        return 1

    def expire(self, key, ttl):
        self._check()
        if key not in self.store:
            return 0
        value, _old = self.store[key]
        self.store[key] = (value, time.time() + ttl)
        return 1

    def ttl(self, key):
        self._check()
        if key not in self.store:
            return -2
        _value, expires = self.store[key]
        return int(expires - time.time()) if expires else -1

    def hset(self, key, mapping=None, *args):
        self._check()
        self.hashes.setdefault(key, {})
        if mapping:
            self.hashes[key].update({str(k): str(v) for k, v in mapping.items()})
        elif len(args) == 2:
            self.hashes[key][str(args[0])] = str(args[1])
        return 1

    def hgetall(self, key):
        self._check()
        return dict(self.hashes.get(key, {}))

    def sadd(self, key, value):
        self._check()
        current = self.store.get(key, ("[]", None))[0]
        values = set(json.loads(current))
        values.add(value)
        self.store[key] = (json.dumps(sorted(values)), None)
        return 1

    def smembers(self, key):
        self._check()
        current = self.store.get(key, ("[]", None))[0]
        return set(json.loads(current))

    def srem(self, key, value):
        self._check()
        current = self.store.get(key, ("[]", None))[0]
        values = set(json.loads(current))
        values.discard(value)
        self.store[key] = (json.dumps(sorted(values)), None)
        return 1

    def xadd(self, key, fields):
        self._check()
        entries = self.streams.setdefault(key, [])
        eid = f"{len(entries) + 1}-0"
        entries.append((eid, {str(k): str(v) for k, v in fields.items()}))
        return eid

    def xrange(self, key, count=50):
        self._check()
        return self.streams.get(key, [])[:count]

    def xdel(self, key, stream_id):
        self._check()
        self.streams[key] = [e for e in self.streams.get(key, []) if e[0] != stream_id]
        return 1

    def xlen(self, key):
        self._check()
        return len(self.streams.get(key, []))

    def scan_iter(self, match=None, count=100):
        self._check()
        prefix = (match or "").rstrip("*")
        for key in list(self.store.keys()) + list(self.hashes.keys()):
            if not match or key.startswith(prefix):
                yield key

    def eval(self, script, numkeys, *args):
        self._check()
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        if "SET', key, ARGV[i], 'NX', 'EX', ttl" in script:
            ttl = int(argv[-1])
            acquired = []
            for i, key in enumerate(keys):
                if self.set(key, argv[i], nx=True, ex=ttl):
                    acquired.append(key)
                else:
                    for ak in acquired:
                        self.delete(ak)
                    return [0, key, self.get(key) or ""]
            return [1, "", ""]
        if "EXPIRE', KEYS[1], ARGV[3]" in script:
            current = self.get(keys[0])
            if not current:
                return 0
            data = json.loads(current)
            if data.get("worker_id") == argv[0] and data.get("job_id") == argv[1]:
                return self.expire(keys[0], int(argv[2]))
            return 0
        if "DELETE', KEYS[1]" in script:
            raise AssertionError("lua release used invalid Redis DELETE command")
        if "DEL', KEYS[1]" in script:
            current = self.get(keys[0])
            if not current:
                return 1
            data = json.loads(current)
            if data.get("worker_id") == argv[0] and data.get("job_id") == argv[1]:
                return self.delete(keys[0])
            return 0
        raise AssertionError("unknown lua script")


def sample_job(job_id="job-1", pr="123", branch="feature"):
    return {
        "job_id": job_id,
        "repo": "org/repo",
        "pr_number": pr,
        "head_branch": branch,
        "priority": "P2",
        "type": "review_response",
    }


def test_duplicate_job_and_pr_leases_block() -> None:
    r = FakeRedis()
    job = sample_job()
    ok, keys = rb.acquire_job_leases(r, "c", job, "worker-a", 60)
    assert ok and len(keys) == 4
    ok2, keys2 = rb.acquire_job_leases(r, "c", {**job, "job_id": "job-2"}, "worker-b", 60)
    assert not ok2 and keys2 == []
    assert rb.get_lease(r, rb.lease_job("c", "job-2")) is None


def test_lease_expiry_allows_reassignment() -> None:
    r = FakeRedis()
    job = sample_job()
    assert rb.acquire_job_leases(r, "c", job, "worker-a", 1)[0]
    time.sleep(1.1)
    assert rb.acquire_job_leases(r, "c", {**job, "job_id": "job-2"}, "worker-b", 60)[0]


def test_worker_crash_release_is_owner_checked() -> None:
    r = FakeRedis()
    job = sample_job()
    assert rb.acquire_job_leases(r, "c", job, "worker-a", 60)[0]
    failed = rb.release_job_leases(r, "c", job["job_id"], job["repo"], "123", "feature", "worker-b")
    assert failed
    assert rb.get_lease(r, rb.lease_job("c", "job-1"))["worker_id"] == "worker-a"
    failed = rb.release_job_leases(r, "c", job["job_id"], job["repo"], "123", "feature", "worker-a")
    assert failed == []
    assert rb.get_lease(r, rb.lease_job("c", "job-1")) is None


def test_path_lock_renewal_and_wrong_owner_failure() -> None:
    r = FakeRedis()
    ok, blocked = rb.acquire_path_locks_atomic(r, "c", "org_repo", "worker-a", "job-1", ["src/a.py"], 1)
    assert ok and blocked == []
    key = rb.lease_path("c", "org_repo", "src/a.py")
    assert rb.renew_path_locks(r, [key], "worker-a", "job-1", 60) == []
    assert rb.renew_path_locks(r, [key], "worker-b", "job-1", 60) == [key]


def test_release_path_locks_uses_valid_redis_del() -> None:
    r = FakeRedis()
    assert rb.acquire_path_locks_atomic(r, "c", "org_repo", "worker-a", "job-1", ["src/a.py"], 60)[0]
    key = rb.lease_path("c", "org_repo", "src/a.py")
    assert rb.release_path_locks(r, [key], "worker-a", "job-1") == []
    assert rb.get_lease(r, key) is None


def test_readonly_advisory_jobs_do_not_require_mutating_mode() -> None:
    assert coordinator._mode_allowed("same_file_review_assist", []) is True
    assert coordinator._mode_allowed("review_response", []) is False
    assert coordinator._mode_allowed("review_response", ["review_response"]) is True


def test_redis_outage_raises_instead_of_silent_success() -> None:
    r = FakeRedis()
    r.down = True
    try:
        rb.acquire_job_leases(r, "c", sample_job(), "worker-a", 60)
    except RedisDown:
        return
    raise AssertionError("redis outage should not look like a successful lease")


def main() -> int:
    tests = [
        test_duplicate_job_and_pr_leases_block,
        test_lease_expiry_allows_reassignment,
        test_worker_crash_release_is_owner_checked,
        test_path_lock_renewal_and_wrong_owner_failure,
        test_release_path_locks_uses_valid_redis_del,
        test_readonly_advisory_jobs_do_not_require_mutating_mode,
        test_redis_outage_raises_instead_of_silent_success,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
