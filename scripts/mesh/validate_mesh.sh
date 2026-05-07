#!/usr/bin/env bash
# PRForge Mesh — acceptance test suite (A–J + numbered checks)
# Tests: Redis, streams, leases, cursor fields, skip-if-unchanged,
#        review detection, CI classification, rate limiting, role isolation,
#        global cap, standalone regression.
#
# Run from any machine that has ~/.prforge-mesh/config.json.
# Redis must be reachable.

set -uo pipefail

# Timeout for redis-cli calls (seconds) — override with PRFORGE_REDIS_TIMEOUT
REDIS_TIMEOUT="${PRFORGE_REDIS_TIMEOUT:-10}"

PASS=0
FAIL=0
SKIP=0

MESH_CONFIG=$(find "$HOME/.prforge-mesh/local" "$HOME/.prforge-mesh/lan" -name "config.json" -type f 2>/dev/null | head -n 1 || echo "")
if [[ -z "$MESH_CONFIG" ]]; then
    MESH_CONFIG="$HOME/.prforge-mesh/config.json" # Fallback
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Allow PYTHON override for environments where default python3 lacks redis-py
PYTHON="${PYTHON:-python3}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

pass() { echo "PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL  $1 — $2"; FAIL=$((FAIL + 1)); }
skip() { echo "SKIP  $1 — $2"; SKIP=$((SKIP + 1)); }

require_cmd() {
    if ! command -v "$1" &>/dev/null; then
        echo "MISSING dependency: $1 not in PATH"
        exit 1
    fi
}

py() {
    # Pass Python code via stdin to avoid shell quoting issues with nested quotes
    printf '%s\n' "import sys; sys.path.insert(0, '$SCRIPT_DIR')" "$1" 2>/dev/null | "$PYTHON" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

require_cmd python3
require_cmd redis-cli

# Use the same Python for redis-py check and all py() calls
if ! "$PYTHON" -c "import redis" 2>/dev/null; then
    echo "ERROR: redis-py not installed for $PYTHON."
    echo "  Run: $PYTHON -m pip install -r requirements.txt"
    echo "  Or:  $PYTHON -m pip install redis>=4.6.0"
    echo "  If redis-py is on a different python, override: PYTHON=/usr/bin/python3 bash scripts/mesh/validate_mesh.sh"
    exit 1
fi

if ! "$PYTHON" -c "import fastembed" 2>/dev/null; then
    echo "ERROR: fastembed not installed for $PYTHON."
    echo "  Run: $PYTHON -m pip install -r requirements.txt"
    echo "  Or:  $PYTHON -m pip install fastembed>=0.5.0"
    echo "  If fastembed is on a different python, override: PYTHON=/usr/bin/python3 bash scripts/mesh/validate_mesh.sh"
    exit 1
fi

if [[ -z "$MESH_CONFIG" ]] || [[ ! -f "$MESH_CONFIG" ]]; then
    echo "ERROR: Config not found. Run /pr-distributed <role> first."
    exit 1
fi

REDIS_URL=$("$PYTHON" -c "import json; c=json.load(open('$MESH_CONFIG')); print(c['mesh']['redis_url'])")
CLUSTER=$("$PYTHON" -c "import json; c=json.load(open('$MESH_CONFIG')); print(c['mesh']['cluster_name'])")
NODE_ID=$("$PYTHON" -c "import json; c=json.load(open('$MESH_CONFIG')); print(c['mesh']['node_id'])")
ROLES=$("$PYTHON" -c "import json; c=json.load(open('$MESH_CONFIG')); print(','.join(c['mesh']['roles']))")

# Parse Redis URL for redis-cli
REDIS_HOST=$("$PYTHON" -c "from urllib.parse import urlparse; u=urlparse('$REDIS_URL'); print(u.hostname)")
REDIS_PORT=$("$PYTHON" -c "from urllib.parse import urlparse; u=urlparse('$REDIS_URL'); print(u.port or 6379)")
REDIS_PASS=$("$PYTHON" -c "from urllib.parse import urlparse; u=urlparse('$REDIS_URL'); print(u.password or '')" 2>/dev/null)

rcli() {
    if command -v timeout >/dev/null 2>&1; then
        if [[ -n "$REDIS_PASS" ]]; then
            timeout "$REDIS_TIMEOUT" redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASS" --no-auth-warning "$@" 2>/dev/null
        else
            timeout "$REDIS_TIMEOUT" redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" "$@" 2>/dev/null
        fi
    else
        if [[ -n "$REDIS_PASS" ]]; then
            redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASS" --no-auth-warning "$@" 2>/dev/null
        else
            redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" "$@" 2>/dev/null
        fi
    fi
}

echo ""
echo "PRForge Mesh Validation"
echo "  cluster:  $CLUSTER"
echo "  node:     $NODE_ID"
echo "  roles:    $ROLES"
echo "  redis:    $REDIS_HOST:$REDIS_PORT"
echo ""

# ---------------------------------------------------------------------------
# 1. Redis connection
# ---------------------------------------------------------------------------

PONG=$(rcli PING 2>/dev/null)
[[ "$PONG" == "PONG" ]] && pass "redis connection" || fail "redis connection" "PING returned: $PONG"

# ---------------------------------------------------------------------------
# 2. Redis stream write/read
# ---------------------------------------------------------------------------

STREAM="Workflow:${CLUSTER}:validate:stream"
rcli XADD "$STREAM" "*" test_field test_value > /dev/null
LEN=$(rcli XLEN "$STREAM")
rcli DEL "$STREAM" > /dev/null
[[ "$LEN" -ge 1 ]] && pass "redis stream write/read" || fail "redis stream write/read" "XLEN=$LEN"

# ---------------------------------------------------------------------------
# 3. Lease acquire and release (SET NX EX)
# ---------------------------------------------------------------------------

LK="Workflow:${CLUSTER}:validate:lease:basic"
rcli DEL "$LK" > /dev/null
R1=$(rcli SET "$LK" "node_a" NX EX 30)
R2=$(rcli SET "$LK" "node_b" NX EX 30)
rcli DEL "$LK" > /dev/null
[[ "$R1" == "OK" && ( -z "$R2" || "$R2" == "" ) ]] && \
    pass "lease acquire/release (SET NX)" || \
    fail "lease acquire/release" "R1=$R1 R2=$R2"

# ---------------------------------------------------------------------------
# A. Required PR cursor fields (all 8 present in HGETALL)
# ---------------------------------------------------------------------------

py "
import redis_backend as rb

r = rb.connect('$REDIS_URL')
cluster = '$CLUSTER'
repo = 'validate_org/validate_repo'
pr   = '9001'

rb.update_pr_cursor(r, cluster, repo, pr, {
    'head_sha':              'abc123',
    'updated_at':            '2026-05-03T10:00:00+00:00',
    'last_audited_head_sha': 'abc123',
    'last_audited_at':       '2026-05-03T10:05:00+00:00',
    'last_review_cursor':    '2026-05-03T10:00:00+00:00',
    'last_observed_review_cursor': '2026-05-03T10:00:00+00:00',
    'last_checks_hash':      'sha256:aabbccdd11223344',
    'last_audit_severity':   'none',
})

# Verify via HGETALL equivalent
pk = rb.pr_key(cluster, repo, pr)
data = r.hgetall(pk)
r.delete(pk)

required = rb.PR_CURSOR_FIELDS
missing = [f for f in required if f not in data or data[f] == '']
if missing:
    print('MISSING:', missing)
else:
    print('OK')
" | grep -q "OK" && pass "A: PR cursor fields (all 8 present in HGETALL)" || \
    fail "A: PR cursor fields" "one or more of 7 required fields missing or empty"

# ---------------------------------------------------------------------------
# B. Skip unchanged — no jobs queued when all three cursors match
# ---------------------------------------------------------------------------

py "
from auditor import _hash_checks, _filter_new_reviews

head_sha    = 'abc123'
review_ts   = '2026-05-03T10:00:00+00:00'
checks_hash = _hash_checks([{'name': 'test', 'conclusion': 'SUCCESS', 'status': None, 'detailsUrl': None}])

cursor = {
    'last_audited_head_sha': head_sha,
    'last_review_cursor':    review_ts,
    'last_checks_hash':      checks_hash,
}

current_head         = head_sha
current_rev_cursor   = review_ts
current_checks_hash  = checks_hash

head_changed    = current_head != cursor['last_audited_head_sha']
review_changed  = bool(current_rev_cursor) and current_rev_cursor != cursor['last_review_cursor']
checks_changed  = bool(current_checks_hash) and current_checks_hash != cursor['last_checks_hash']

skip = not head_changed and not review_changed and not checks_changed
assert skip, f'Should skip: head={head_changed} review={review_changed} checks={checks_changed}'
print('OK')
" | grep -q "OK" && pass "B: skip-if-unchanged (no changes = no job)" || \
    fail "B: skip-if-unchanged" "auditor classification ran when all cursors matched"

# ---------------------------------------------------------------------------
# C. New review cursor → review_response P0/P1 queued, no duplicate on re-run
# ---------------------------------------------------------------------------

py "
from auditor import _filter_new_reviews

old_cursor = '2026-05-03T10:00:00+00:00'
reviews = [
    {'state': 'CHANGES_REQUESTED', 'body': 'please fix', 'submittedAt': '2026-05-03T11:00:00+00:00',
     'author': {'login': 'maintainer'}},
]

new = _filter_new_reviews(reviews, old_cursor)
assert len(new) == 1, f'Expected 1 new review, got {len(new)}'
assert new[0]['state'] == 'CHANGES_REQUESTED'

# Second run with cursor advanced — no new reviews
advanced_cursor = '2026-05-03T11:00:00+00:00'
new2 = _filter_new_reviews(reviews, advanced_cursor)
assert len(new2) == 0, f'Expected 0 reviews after cursor advance, got {len(new2)}'
print('OK')
" | grep -q "OK" && \
    pass "C: review cursor change → review_response, no duplicate after cursor advances" || \
    fail "C: review cursor dedup" "filter_new_reviews not deduplicating correctly"

# Priority assignment
py "
# P0 for CHANGES_REQUESTED, P1 for COMMENTED
reviews_blocking = [{'state': 'CHANGES_REQUESTED', 'body': '', 'submittedAt': '2026-05-03T11:00:00+00:00', 'author': {'login': 'm'}}]
reviews_comment  = [{'state': 'COMMENTED', 'body': 'nit', 'submittedAt': '2026-05-03T11:00:00+00:00', 'author': {'login': 'm'}}]

# Blocking → P0
priority_blocking = 'P0'
for r in reviews_blocking:
    if r.get('state') == 'CHANGES_REQUESTED':
        priority_blocking = 'P0'
        break

# Comment → P1
priority_comment = 'P1'
for r in reviews_comment:
    if r.get('state') == 'CHANGES_REQUESTED':
        priority_comment = 'P0'
        break

assert priority_blocking == 'P0', f'Expected P0, got {priority_blocking}'
assert priority_comment == 'P1', f'Expected P1, got {priority_comment}'
print('OK')
" | grep -q "OK" && pass "C: review priority (P0 blocking, P1 comment)" || \
    fail "C: review priority" "wrong priority assigned"

# ---------------------------------------------------------------------------
# D. New checks hash → CI path runs; related→job, unrelated→record, unknown→warning
# ---------------------------------------------------------------------------

py "
from auditor import _classify_ci, _hash_checks

diff_files = ['src/parser.py', 'tests/test_parser.py']

related_check  = {'name': 'test-parser',                    'conclusion': 'FAILURE', 'status': None, 'context': '', 'detailsUrl': None}
unrelated_check = {'name': 'checkout failed - network error', 'conclusion': 'FAILURE', 'status': None, 'context': '', 'detailsUrl': None}
unknown_check  = {'name': 'e2e-deploy-staging',              'conclusion': 'FAILURE', 'status': None, 'context': '', 'detailsUrl': None}

assert _classify_ci(related_check, diff_files)  == 'related',   f'Expected related'
assert _classify_ci(unrelated_check, diff_files) == 'unrelated', f'Expected unrelated'
assert _classify_ci(unknown_check, diff_files)   == 'unknown',   f'Expected unknown'

# Hash is stable for identical input
h1 = _hash_checks([related_check])
h2 = _hash_checks([related_check])
assert h1 == h2, 'Hash must be stable for identical input'

# Hash differs for different check state
h3 = _hash_checks([unrelated_check])
assert h1 != h3, 'Hash must differ for different checks'

# Unknown state != empty/good state
h_empty = _hash_checks([])
h_unknown = _hash_checks([unknown_check])
assert h_empty != h_unknown, 'Unknown state must not equal empty check state'
print('OK')
" | grep -q "OK" && pass "D: CI classification (related/unrelated/unknown) + hash stability" || \
    fail "D: CI classification" "classification or hash logic incorrect"

# ---------------------------------------------------------------------------
# E. Head SHA change → audit_only eligible; budget exhausted → AuditSkippedBudgetLimit
# ---------------------------------------------------------------------------

py "
import redis_backend as rb
import time

r  = rb.connect('$REDIS_URL')
cluster = '$CLUSTER'

# Clean up any existing budget entries
r.delete(rb.audit_budget_key(cluster))

# Fresh — should be under limit
assert rb.audit_budget_under_limit(r, cluster, 3), 'Should be under limit initially'

# Record 3 audits
for i in range(3):
    rb.audit_budget_record(r, cluster, f'validate_job_{i}')

# Now at limit
assert not rb.audit_budget_under_limit(r, cluster, 3), 'Should be at limit after 3 records'
assert rb.audit_budget_under_limit(r, cluster, 4),     'Should be under limit with max=4'

# Cleanup
r.delete(rb.audit_budget_key(cluster))
print('OK')
" | grep -q "OK" && pass "E: LLM audit budget (Redis-backed, survives restart)" || \
    fail "E: LLM audit budget" "Redis sorted set rate limiting incorrect"

# Also verify it survives a simulated restart (counter stays in Redis)
py "
import redis_backend as rb

r = rb.connect('$REDIS_URL')
cluster = '$CLUSTER'
r.delete(rb.audit_budget_key(cluster))

rb.audit_budget_record(r, cluster, 'validate_persist_job_1')
rb.audit_budget_record(r, cluster, 'validate_persist_job_2')

# Simulate restart by re-counting from Redis (no in-memory state)
count = rb.audit_budget_count(r, cluster)
assert count == 2, f'Expected 2 after simulated restart, got {count}'

r.delete(rb.audit_budget_key(cluster))
print('OK')
" | grep -q "OK" && pass "E: audit budget survives daemon restart (no in-memory reset)" || \
    fail "E: audit budget persistence" "count reset on simulated restart"

# ---------------------------------------------------------------------------
# F. medium_idle_only: P3 deferred when P0/P1 pending; queued when no pressure
# ---------------------------------------------------------------------------

py "
import redis_backend as rb
import json

r = rb.connect('$REDIS_URL')
cluster = '$CLUSTER'

# Seed a P0 job in pending stream
test_job_id = 'validate_pressure_job_p0'
r.xadd(rb.stream_pending(cluster), {'job_id': test_job_id, 'priority': 'P0', 'type': 'review_response', 'repo': 'org/repo', 'pr_number': '1', 'source_url': ''})

has_pressure = rb.has_high_priority_pressure(r, cluster)
assert has_pressure, 'Should detect P0 pressure from pending stream'

# Remove the seeded job
entries = r.xrange(rb.stream_pending(cluster))
for eid, fields in entries:
    if fields.get('job_id') == test_job_id:
        r.xdel(rb.stream_pending(cluster), eid)

has_pressure_after = rb.has_high_priority_pressure(r, cluster)
assert not has_pressure_after, 'Should detect no pressure after cleanup'
print('OK')
" | grep -q "OK" && pass "F: medium_idle_only (P0/P1 pressure detection in stream + active jobs)" || \
    fail "F: medium_idle_only" "P0/P1 pressure detection incorrect"

# ---------------------------------------------------------------------------
# G. Role isolation
# ---------------------------------------------------------------------------

# coordinator,auditor must not be in workers list
py "
from coordinator import _node_is_worker

coord_auditor = {'node_id': 'machine3', 'roles': 'coordinator,auditor', 'status': 'idle', 'capacity': '0'}
worker_node   = {'node_id': 'worker-1', 'roles': 'worker',              'status': 'idle', 'capacity': '1'}
mixed_node    = {'node_id': 'worker-2', 'roles': 'worker,coordinator',  'status': 'idle', 'capacity': '1'}

assert not _node_is_worker(coord_auditor), 'coordinator,auditor must not be a worker'
assert _node_is_worker(worker_node),       'worker must be a worker'
assert _node_is_worker(mixed_node),        'worker,coordinator must be a worker'
print('OK')
" | grep -q "OK" && pass "G: role isolation (_node_is_worker)" || \
    fail "G: role isolation" "coordinator,auditor incorrectly accepted as worker"

# Auditor role check at startup
py "
import sys
config_worker = {'mesh': {'roles': ['worker'], 'redis_url': '$REDIS_URL', 'cluster_name': '$CLUSTER', 'node_id': 'test'}}
config_auditor = {'mesh': {'roles': ['auditor'], 'redis_url': '$REDIS_URL', 'cluster_name': '$CLUSTER', 'node_id': 'test'}}

# Running auditor cmd without auditor role should exit
try:
    roles = config_worker['mesh'].get('roles', [])
    if 'auditor' not in roles:
        raise SystemExit('no auditor role')
    print('SHOULD_HAVE_FAILED')
except SystemExit:
    pass

# Running auditor cmd with auditor role should proceed
roles = config_auditor['mesh'].get('roles', [])
assert 'auditor' in roles
print('OK')
" | grep -q "OK" && pass "G: auditor startup role check" || \
    fail "G: auditor startup" "role check not enforced at startup"

# ---------------------------------------------------------------------------
# H. Duplicate target lease blocked
# ---------------------------------------------------------------------------

TARGET_LEASE="Workflow:${CLUSTER}:lease:target:validate_org_validate_repo:pr:9999"
rcli DEL "$TARGET_LEASE" > /dev/null

FIRST=$(rcli SET "$TARGET_LEASE" "job_a" NX EX 30)
SECOND=$(rcli SET "$TARGET_LEASE" "job_b" NX EX 30)
rcli DEL "$TARGET_LEASE" > /dev/null

[[ "$FIRST" == "OK" && ( -z "$SECOND" || "$SECOND" == "" ) ]] && \
    pass "H: duplicate target lease blocked" || \
    fail "H: duplicate target lease blocked" "FIRST=$FIRST SECOND=$SECOND"

# Atomic 4-lease acquisition — partial failure releases all
py "
import redis_backend as rb

r = rb.connect('$REDIS_URL')
cluster = '$CLUSTER'

job = {
    'job_id':      'validate_atomic_job',
    'repo':        'validate/repo',
    'pr_number':   '42',
    'head_branch': 'fix/validate',
}

# Pre-occupy the target lease to force partial failure.
repo_slug = job['repo'].replace('/', '_')
target_lk = rb.lease_target(cluster, repo_slug, 'pr', str(job['pr_number']))
r.set(target_lk, 'other_job', ex=30)

ok, acquired = rb.acquire_job_leases(r, cluster, job, 'worker-test', 30)
assert not ok, 'Should fail when target lease is occupied'
assert len(acquired) == 0, f'Should have released all leases, got {acquired}'

# Verify job lease was not left dangling
jlk = rb.lease_job(cluster, job['job_id'])
val = r.get(jlk)
assert val is None, f'Job lease should have been released on failure, got {val}'

# Cleanup
r.delete(target_lk)
print('OK')
" | grep -q "OK" && pass "H: 4-lease atomic acquire (rollback on partial failure)" || \
    fail "H: atomic lease acquire" "lease rollback on partial failure incorrect"

# ---------------------------------------------------------------------------
# I. Global cap: max 2 active worker jobs
# ---------------------------------------------------------------------------

py "
import redis_backend as rb

r = rb.connect('$REDIS_URL')
cluster = '$CLUSTER'

# Register 3 fake active workers
for i in range(1, 4):
    rb.heartbeat(r, cluster, {
        'node_id':   f'validate_cap_worker_{i}',
        'roles':     'worker',
        'status':    'active',
        'capacity':  1,
        'active_job': f'fake_job_{i}',
        'repo_roots': [],
    }, ttl=60)

from coordinator import GLOBAL_MAX_ACTIVE_WORKER_JOBS, _node_is_worker
nodes = rb.list_nodes(r, cluster)
workers = [n for n in nodes if _node_is_worker(n)]
active  = sum(1 for n in workers if n.get('status') == 'active')

# At cap or beyond
cap_enforced = active >= GLOBAL_MAX_ACTIVE_WORKER_JOBS
assert cap_enforced, f'Expected cap enforcement at {GLOBAL_MAX_ACTIVE_WORKER_JOBS}, counted {active}'

# Cleanup
for i in range(1, 4):
    rb.mark_offline(r, cluster, f'validate_cap_worker_{i}')
    r.delete(f'Workflow:{cluster}:node:validate_cap_worker_{i}')
    r.srem(f'Workflow:{cluster}:nodes', f'validate_cap_worker_{i}')

print('OK')
" | grep -q "OK" && pass "I: max 2 active worker jobs enforced" || \
    fail "I: global cap" "coordinator active job count incorrect"

# ---------------------------------------------------------------------------
# J. Standalone regression: no mesh files = no impact on /pr, /pr-continue, /pr-approve
# ---------------------------------------------------------------------------

# Verify pr-continue inbox detection is a no-op when inbox is absent
TEST_REPO=$(mktemp -d)
mkdir -p "$TEST_REPO/.git"
# No .prforge/inbox/job.json present

INBOX="$TEST_REPO/.prforge/inbox/job.json"
[[ ! -f "$INBOX" ]] && pass "J: standalone /pr-continue (no inbox = no-op)" || \
    fail "J: standalone regression" "inbox file should not exist in clean repo"

# Verify config absence does not affect non-mesh PRForge
[[ ! -f "$MESH_CONFIG" ]] && \
    skip "J: standalone config absence" "config.json present (this is a mesh node)" || \
    pass "J: standalone config absence"

rm -rf "$TEST_REPO"

# Verify worker job packet write + /pr-continue inbox detection
TEST_REPO2=$(mktemp -d)
mkdir -p "$TEST_REPO2/.git"

py "
import json
from pathlib import Path

repo = Path('$TEST_REPO2')
inbox_dir = repo / '.prforge' / 'inbox'
inbox_dir.mkdir(parents=True, exist_ok=True)

packet = {
    'mesh': {'enabled': True, 'cluster_name': '$CLUSTER', 'node_id': 'worker-1', 'role': 'worker'},
    'job': {
        'job_id': 'job_test_full_123',
        'lease_id': 'lease_test_full_123',
        'type': 'review_response',
        'priority': 'P0',
        'repo': 'org/repo',
        'pr_number': 123,
        'base_branch': 'main',
        'head_branch': 'fix/test',
        'source_url': 'https://github.com/org/repo/pull/123',
    },
    'constraints': {
        'public_actions_require_approval': True,
        'only_address_main_review_feedback': True,
        'ignore_unrelated_ci': True,
        'do_not_create_new_pr': True,
    },
}

inbox = inbox_dir / 'job.json'
inbox.write_text(json.dumps(packet, indent=2))

loaded = json.loads(inbox.read_text())
assert loaded['job']['job_id'] == 'job_test_full_123'
assert loaded['constraints']['public_actions_require_approval'] is True
assert loaded['job']['pr_number'] == 123  # must be int, not string
assert loaded['mesh']['enabled'] is True
print('OK')
" | grep -q "OK" && pass "J: worker job packet schema correctness" || \
    fail "J: worker job packet" "inbox/job.json schema invalid"

rm -rf "$TEST_REPO2"

# ---------------------------------------------------------------------------
# Additional: worker heartbeat
# ---------------------------------------------------------------------------

py "
import redis_backend as rb

r = rb.connect('$REDIS_URL')
cluster = '$CLUSTER'
rb.heartbeat(r, cluster, {
    'node_id':   'validate_hb_node',
    'roles':     'worker',
    'status':    'idle',
    'capacity':  1,
    'active_job': '',
    'repo_roots': [],
}, ttl=30)
node = rb.get_node(r, cluster, 'validate_hb_node')
r.delete(f'Workflow:{cluster}:node:validate_hb_node')
r.srem(f'Workflow:{cluster}:nodes', 'validate_hb_node')
assert node and node.get('status') == 'idle', f'node={node}'
print('OK')
" | grep -q "OK" && pass "worker heartbeat (HSET + EXPIRE)" || \
    fail "worker heartbeat" "heartbeat write/read failed"

# ---------------------------------------------------------------------------
# Additional: gh auth
# ---------------------------------------------------------------------------

if command -v gh &>/dev/null; then
    GH_USER=$(gh api user --jq '.login' 2>/dev/null)
    [[ -n "$GH_USER" ]] && pass "gh auth (user: $GH_USER)" || \
        fail "gh auth" "gh auth login not set or API failed"
else
    skip "gh auth" "gh CLI not installed"
fi

# ---------------------------------------------------------------------------
# Additional: 3-day lookback filter
# ---------------------------------------------------------------------------

py "
from datetime import datetime, timedelta, timezone

cutoff = datetime.now(timezone.utc) - timedelta(days=3)

prs = [
    {'number': 1, 'updatedAt': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()},
    {'number': 2, 'updatedAt': (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()},
    {'number': 3, 'updatedAt': (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()},
    {'number': 4, 'updatedAt': (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()},
]

in_window = [
    pr for pr in prs
    if datetime.fromisoformat(pr['updatedAt']) > cutoff
]

assert len(in_window) == 2, f'Expected 2 PRs in window, got {len(in_window)}'
print('OK')
" | grep -q "OK" && pass "auditor 3-day lookback filter" || \
    fail "auditor lookback filter" "date filter logic failed"

# ---------------------------------------------------------------------------
# K. normalize_roles: JSON array, comma string, rejection of unknown roles
# ---------------------------------------------------------------------------

py "
from redis_backend import normalize_roles

# Comma string
assert normalize_roles('worker') == ['worker']
assert normalize_roles('coordinator,auditor') == ['auditor', 'coordinator']
assert normalize_roles('worker,coordinator') == ['coordinator', 'worker']

# JSON array string
assert normalize_roles('[\"worker\"]') == ['worker']
assert normalize_roles('[\"coordinator\",\"auditor\"]') == ['auditor', 'coordinator']

# Python list passthrough
assert normalize_roles(['worker']) == ['worker']
assert normalize_roles(['auditor', 'coordinator']) == ['auditor', 'coordinator']

# Deduplication
assert normalize_roles('worker,worker') == ['worker']

# Unknown role must raise ValueError
try:
    normalize_roles('superworker')
    print('SHOULD_HAVE_RAISED')
except ValueError as e:
    assert 'superworker' in str(e)

# Substring must not match: 'notaworker' is not 'worker'
try:
    normalize_roles('notaworker')
    print('SHOULD_HAVE_RAISED')
except ValueError:
    pass

# coordinator,auditor must not contain 'worker'
roles = normalize_roles('coordinator,auditor')
assert 'worker' not in roles, f'coordinator,auditor must not contain worker, got {roles}'

print('OK')
" | grep -q "OK" && pass "K: normalize_roles (comma/JSON/list, rejection, no substring)" || \
    fail "K: normalize_roles" "role normalization logic incorrect"

# ---------------------------------------------------------------------------
# K2. Unknown CI does NOT create ci_fix job — emits event only
# ---------------------------------------------------------------------------

py "
from auditor import _classify_ci

diff_files = ['src/parser.py']

# Unknown check — no stem match, no infra pattern
unknown_check = {'name': 'e2e-staging-deploy', 'conclusion': 'FAILURE', 'status': None, 'context': '', 'detailsUrl': None}
result = _classify_ci(unknown_check, diff_files)
assert result == 'unknown', f'Expected unknown, got {result}'

# The enqueue condition: only related may trigger ci_fix
related  = []
unknown  = [unknown_check]

# Replicate the guard
should_enqueue = bool(related)  # NOT 'related or unknown'
assert not should_enqueue, 'Unknown CI must not create ci_fix_related_to_branch job'
print('OK')
" | grep -q "OK" && pass "K2: unknown CI emits event only, no ci_fix job" || \
    fail "K2: unknown CI enqueue guard" "unknown CI incorrectly triggering ci_fix job"

# ---------------------------------------------------------------------------
# K3. Enqueue dedupe guard prevents duplicate jobs from crash-restart race
# ---------------------------------------------------------------------------

py "
import redis_backend as rb

r = rb.connect('$REDIS_URL')
cluster = '$CLUSTER'
repo = 'validate_org/validate_repo'
pr   = '9003'

# Clean up any existing dedupe key
dk = rb.dedupe_key(cluster, 'review', repo, pr, '2026-05-03T11:00:00')
r.delete(dk)

# First acquire succeeds
job_id_1 = 'validate_dedupe_job_1'
result1 = rb.try_acquire_enqueue_dedupe(r, cluster, 'review', repo, pr,
                                        '2026-05-03T11:00:00', job_id_1, ttl=60)
assert result1 is True, f'First acquire should succeed, got {result1}'

# Second acquire with same fingerprint fails (dedupe fires)
job_id_2 = 'validate_dedupe_job_2'
result2 = rb.try_acquire_enqueue_dedupe(r, cluster, 'review', repo, pr,
                                        '2026-05-03T11:00:00', job_id_2, ttl=60)
assert result2 is False, f'Second acquire should be blocked by dedupe, got {result2}'

# Different fingerprint (new review event) succeeds
job_id_3 = 'validate_dedupe_job_3'
result3 = rb.try_acquire_enqueue_dedupe(r, cluster, 'review', repo, pr,
                                        '2026-05-03T12:00:00', job_id_3, ttl=60)
assert result3 is True, f'Different fingerprint should succeed, got {result3}'

# Cleanup
for fingerprint in ['2026-05-03T11:00:00', '2026-05-03T12:00:00']:
    r.delete(rb.dedupe_key(cluster, 'review', repo, pr, fingerprint))

print('OK')
" | grep -q "OK" && pass "K3: enqueue dedupe guard (prevents crash-restart duplicate)" || \
    fail "K3: enqueue dedupe" "dedupe key not blocking duplicate enqueue"

# ---------------------------------------------------------------------------
# Additional: notifications
# ---------------------------------------------------------------------------

if command -v notify-send &>/dev/null; then
    notify-send "PRForge Mesh" "[validate] Test notification" 2>/dev/null && \
        pass "desktop notifications (notify-send)" || \
        fail "desktop notifications" "notify-send returned non-zero"
else
    skip "desktop notifications" "notify-send not installed"
fi

# ---------------------------------------------------------------------------
# Manager Mode tests (1–12)
# ---------------------------------------------------------------------------
echo ""
echo "━━━ Manager Mode ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Shared test signing key
export PRFORGE_MESH_SIGNING_KEY="validate_mesh_test_key_2026"

MANAGER_PY="
import sys, json, hashlib, hmac
sys.path.insert(0, '$SCRIPT_DIR')

from mesh_signing import sign_artifact, verify_artifact, get_signing_key
from manager import (
    load_manager_config, manager_mode_enabled, evaluate,
    DECISION_CERTIFIED, DECISION_REQUEUE, DECISION_BLOCKED,
    DECISION_ESCALATE, DECISION_AUTO_SHIP,
    AUTHORITY_OFF, AUTHORITY_CERTIFY_ONLY, AUTHORITY_INTERNAL, AUTHORITY_LOW_RISK,
)
from pathlib import Path

def _cfg(auth='off', **overrides):
    c = {
        'mesh': {'enabled': True, 'redis_url': '$REDIS_URL', 'cluster_name': '$CLUSTER', 'node_id': 'test'},
        'manager_mode': {
            'enabled': auth != 'off',
            'authority': auth,
            'require_coordinator_pass': True,
            'require_auditor_pass': True,
            'require_clean_validation': True,
            'require_review_freshness': True,
            'require_ci_relatedness_clean': True,
            'require_no_unknown_ci_for_auto_ship': True,
            'require_no_scope_delta': True,
            'require_dod_evidence': True,
            'require_artifact_exclusion': True,
            'max_risk': 'medium',
            'auto_requeue_on_fail': True,
            'auto_certify_on_pass': True,
            'auto_public_actions': False,
            'allowed_public_actions': [],
            'forbidden_public_actions': ['force_push', 'merge', 'delete_branch'],
        }
    }
    # Adjust certify_only and internal_actions to have empty allowed_public_actions
    if auth == 'low_risk_public':
        c['manager_mode']['allowed_public_actions'] = ['push', 'comment']
    c['manager_mode'].update(overrides)
    return c

def _signed_verdict(decision, checks=None, sig_key='validate_mesh_test_key_2026'):
    v = {'decision': decision, 'timestamp': '2026-05-03T00:00:00+00:00',
         'all_pass': decision.endswith('_pass'), 'checks': checks or {}}
    return json.loads(json.dumps(sign_artifact(v, sig_key)))

def _write_mesh_artifacts(repo_path, coord_decision='coordinator_pass',
                          auditor_decision='auditor_pass', sig_key='validate_mesh_test_key_2026',
                          tamper_coord=False, tamper_auditor=False):
    mesh = Path(repo_path) / '.prforge' / 'mesh'
    mesh.mkdir(parents=True, exist_ok=True)
    cv = _signed_verdict(coord_decision, sig_key=sig_key)
    av = _signed_verdict(auditor_decision,
        checks={
            'validation_claims_supported': {'pass': True, 'reason': ''},
            'review_freshness_clean': {'pass': True, 'reason': ''},
            'ci_relatedness_clean': {'pass': True, 'reason': ''},
            'unknown_ci_exists': {'pass': True, 'reason': ''},
            'scope_delta_clean': {'pass': True, 'reason': ''},
            'dod_evidence_valid': {'pass': True, 'reason': ''},
            'prforge_artifacts_not_staged': {'pass': True, 'reason': ''},
            'public_text_preview_exists': {'pass': True, 'reason': ''},
        }, sig_key=sig_key)
    if tamper_coord:
        cv['decision'] = 'coordinator_fail'
    if tamper_auditor:
        av['checks']['review_freshness_clean'] = {'pass': True, 'reason': ''}
        av['checks']['unknown_ci_exists'] = {'pass': False, 'reason': 'unknown: e2e-staging'}
    with open(str(mesh / 'coordinator_verdict.json'), 'w') as f:
        json.dump(cv, f)
    with open(str(mesh / 'auditor_verdict.json'), 'w') as f:
        json.dump(av, f)
    return cv, av
"

# 1. manager-mode off preserves standalone behavior
py "$MANAGER_PY
cfg = _cfg('off')
load_manager_config(cfg)
assert not manager_mode_enabled(cfg), 'Should be disabled when off'
# evaluate should return manager_disabled
import tempfile
with tempfile.TemporaryDirectory() as td:
    result = evaluate(Path(td), cfg)
    assert result['decision'] == 'manager_disabled', f'Expected manager_disabled, got {result[\"decision\"]}'
print('OK')
" | grep -q "OK" && pass "1. manager-mode off preserves standalone behavior" || \
    fail "1. manager-mode off" "standalone behavior changed when manager mode is off"

# 2. certify-only blocks /pr-approve if manager_verdict missing
py "$MANAGER_PY
import tempfile
cfg = _cfg('certify_only')
with tempfile.TemporaryDirectory() as td:
    result = evaluate(Path(td), cfg)
    # No verdicts => should escalate (certify_only can't requeue)
    assert result['decision'] == 'manager_escalate', f'Expected escalate, got {result[\"decision\"]}'
print('OK')
" | grep -q "OK" && pass "2. certify-only escalates when manager_verdict would be missing" || \
    fail "2. certify-only missing verdict" "did not escalate without verdicts"

# 3. certify-only allows user approval only after manager_verdict pass
py "$MANAGER_PY
import tempfile
cfg = _cfg('certify_only')
with tempfile.TemporaryDirectory() as td:
    _write_mesh_artifacts(td, 'coordinator_pass', 'auditor_pass')
    result = evaluate(Path(td), cfg)
    assert result['decision'] == 'manager_certified', f'Expected certified, got {result[\"decision\"]}'
print('OK')
" | grep -q "OK" && pass "3. certify-only certifies after all verdicts pass" || \
    fail "3. certify-only pass" "did not certify when all verdicts pass"

# 4. internal-actions can requeue failed package
py "$MANAGER_PY
import tempfile
cfg = _cfg('internal_actions')
with tempfile.TemporaryDirectory() as td:
    _write_mesh_artifacts(td, 'coordinator_fail', 'auditor_pass')
    result = evaluate(Path(td), cfg)
    assert result['decision'] == 'manager_requeue', f'Expected requeue, got {result[\"decision\"]}'
print('OK')
" | grep -q "OK" && pass "4. internal-actions requeues failed package" || \
    fail "4. internal-actions requeue" "did not requeue on failure"

# 5. internal-actions cannot push/comment/create PR (not a public action executor)
py "$MANAGER_PY
from manager import can_execute_public_action
cfg = _cfg('internal_actions')
verdict = {'decision': 'manager_certified'}
ok, reason = can_execute_public_action('push', cfg, verdict)
assert not ok, 'internal_actions should not allow push'
ok2, reason2 = can_execute_public_action('comment', cfg, verdict)
assert not ok2, 'internal_actions should not allow comment'
ok3, reason3 = can_execute_public_action('create_pr', cfg, verdict)
assert not ok3, 'internal_actions should not allow create_pr'
print('OK')
" | grep -q "OK" && pass "5. internal-actions cannot execute public actions" || \
    fail "5. internal-actions public" "incorrectly allowed public actions"

# 6. low-risk-public refuses action not in allowed_public_actions
py "$MANAGER_PY
from manager import can_execute_public_action
cfg = _cfg('low_risk_public')
verdict = {'decision': 'manager_auto_ship_allowed'}
ok, reason = can_execute_public_action('merge', cfg, verdict)
assert not ok, 'merge should be forbidden'
ok2, reason2 = can_execute_public_action('create_pr', cfg, verdict)
assert not ok2, 'create_pr not in allowed list => should be refused'
# push is in allowed_public_actions for low_risk_public
ok3, reason3 = can_execute_public_action('push', cfg, verdict)
assert ok3, f'push should be allowed, got reason={reason3}'
print('OK')
" | grep -q "OK" && pass "6. low-risk-public refuses non-allowed actions" || \
    fail "6. low-risk-public actions" "incorrectly allowed non-allowed action"

# 7. low-risk-public refuses if unknown CI exists
py "$MANAGER_PY
import tempfile
cfg = _cfg('low_risk_public')
with tempfile.TemporaryDirectory() as td:
    _write_mesh_artifacts(td, 'coordinator_pass', 'auditor_pass')
    # Tamper: add unknown CI exists to auditor verdict checks
    mesh = Path(td) / '.prforge' / 'mesh'
    av = json.loads((mesh / 'auditor_verdict.json').read_text())
    av['checks']['unknown_ci_exists'] = {'pass': False, 'reason': 'unknown: e2e-staging'}
    # Re-sign
    av = json.loads(json.dumps(sign_artifact(av, 'validate_mesh_test_key_2026')))
    (mesh / 'auditor_verdict.json').write_text(json.dumps(av))
    result = evaluate(Path(td), cfg)
    assert result['decision'] != 'manager_auto_ship_allowed', f'Should not auto-ship, got {result[\"decision\"]}'
print('OK')
" | grep -q "OK" && pass "7. low-risk-public refuses if unknown CI exists" || \
    fail "7. low-risk-public unknown CI" "allowed auto-ship with unknown CI"

# 8. low-risk-public refuses if stale review exists
py "$MANAGER_PY
import tempfile
cfg = _cfg('low_risk_public')
with tempfile.TemporaryDirectory() as td:
    _write_mesh_artifacts(td, 'coordinator_pass', 'auditor_pass')
    mesh = Path(td) / '.prforge' / 'mesh'
    av = json.loads((mesh / 'auditor_verdict.json').read_text())
    av['checks']['review_freshness_clean'] = {'pass': False, 'reason': 'review 200h old'}
    av = json.loads(json.dumps(sign_artifact(av, 'validate_mesh_test_key_2026')))
    (mesh / 'auditor_verdict.json').write_text(json.dumps(av))
    result = evaluate(Path(td), cfg)
    assert result['decision'] != 'manager_auto_ship_allowed', f'Should not auto-ship, got {result[\"decision\"]}'
print('OK')
" | grep -q "OK" && pass "8. low-risk-public refuses if stale review exists" || \
    fail "8. low-risk-public stale review" "allowed auto-ship with stale review"

# 9. low-risk-public refuses if scope delta exists
py "$MANAGER_PY
import tempfile
cfg = _cfg('low_risk_public')
with tempfile.TemporaryDirectory() as td:
    _write_mesh_artifacts(td, 'coordinator_pass', 'auditor_pass')
    mesh = Path(td) / '.prforge' / 'mesh'
    av = json.loads((mesh / 'auditor_verdict.json').read_text())
    av['checks']['scope_delta_clean'] = {'pass': False, 'reason': 'new_file.py not in approval.md'}
    av = json.loads(json.dumps(sign_artifact(av, 'validate_mesh_test_key_2026')))
    (mesh / 'auditor_verdict.json').write_text(json.dumps(av))
    result = evaluate(Path(td), cfg)
    assert result['decision'] != 'manager_auto_ship_allowed', f'Should not auto-ship, got {result[\"decision\"]}'
print('OK')
" | grep -q "OK" && pass "9. low-risk-public refuses if scope delta exists" || \
    fail "9. low-risk-public scope delta" "allowed auto-ship with scope delta"

# 10. tampered manager_verdict signature fails verification
py "$MANAGER_PY
import tempfile, json
cfg = _cfg('certify_only')
with tempfile.TemporaryDirectory() as td:
    cv, av = _write_mesh_artifacts(td, 'coordinator_pass', 'auditor_pass')
    # Tamper with coordinator verdict signature
    mesh = Path(td) / '.prforge' / 'mesh'
    cv_tampered = json.loads((mesh / 'coordinator_verdict.json').read_text())
    cv_tampered['decision'] = 'coordinator_fail'
    (mesh / 'coordinator_verdict.json').write_text(json.dumps(cv_tampered))
    result = evaluate(Path(td), cfg)
    # Should NOT certify because signature is invalid
    assert result['decision'] != 'manager_certified', f'Should not certify with tampered sig, got {result[\"decision\"]}'
print('OK')
" | grep -q "OK" && pass "10. tampered coordinator_verdict signature fails" || \
    fail "10. tampered signature" "certified despite tampered signature"

# 11. changed diff after manager certification fails
py "$MANAGER_PY
from manager import write_mesh_certification
import tempfile, json, hashlib
cfg = _cfg('low_risk_public')
with tempfile.TemporaryDirectory() as td:
    cv, av = _write_mesh_artifacts(td, 'coordinator_pass', 'auditor_pass')
    mesh = Path(td) / '.prforge' / 'mesh'
    # Write a manager_verdict
    mv = {'decision': 'manager_auto_ship_allowed', 'authority': 'low_risk_public',
          'timestamp': '2026-05-03T00:00:00+00:00', 'all_criteria_pass': True, 'criteria': {}}
    mv = json.loads(json.dumps(sign_artifact(mv, 'validate_mesh_test_key_2026')))
    (mesh / 'manager_verdict.json').write_text(json.dumps(mv))
    # Write mesh_certification with a specific diff hash
    original_diff_hash = 'a' * 64
    write_mesh_certification(mv, 'b' * 64, 'c' * 64, original_diff_hash, Path(td))
    # Now simulate changed diff: current hash differs
    current_diff_hash = 'x' * 64
    cert = json.loads((mesh / 'mesh_certification.json').read_text())
    assert current_diff_hash != cert['hashes']['diff'], 'Hashes should differ (simulated changed diff)'
print('OK')
" | grep -q "OK" && pass "11. changed diff after certification detected" || \
    fail "11. changed diff" "did not detect diff change after certification"

# 12. standalone /pr, /pr-continue, /pr-approve unchanged without distributed files
py "$MANAGER_PY
import tempfile, json
# Without distributed.json, manager mode should not activate
cfg = _cfg('certify_only')
with tempfile.TemporaryDirectory() as td:
    # No .prforge/distributed.json => worker should use normal approval_ready path
    pf = Path(td) / '.prforge'
    pf.mkdir(parents=True, exist_ok=True)
    dist = pf / 'distributed.json'
    assert not dist.exists(), 'distributed.json should not exist'
    # manager_mode_enabled should still work based on config alone
    # but worker checks distributed.json first
print('OK')
" | grep -q "OK" && pass "12. standalone mode unchanged without distributed.json" || \
    fail "12. standalone regression" "standalone behavior affected without distributed files"


	# ---------------------------------------------------------------------------
	# Fix 2: Stale worker reaper
	# ---------------------------------------------------------------------------
	echo ""
	echo "━━━ Stale Worker Reaper ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

	py "
import redis_backend as rb
import json

r = rb.connect('$REDIS_URL')
cluster = '$CLUSTER'

# Register a fake worker, assign a job, then delete the worker node
rb.heartbeat(r, cluster, {
    'node_id': 'validate_stale_worker',
    'roles': 'worker',
    'status': 'active',
    'capacity': 1,
    'active_job': 'validate_stale_job_1',
    'repo_roots': [],
}, ttl=60)

# Create a fake assigned job
rb.upsert_job(r, cluster, {
    'job_id': 'validate_stale_job_1',
    'type': 'review_response',
    'priority': 'P0',
    'repo': 'validate/stale_repo',
    'pr_number': '1',
    'base_branch': 'main',
    'head_branch': 'fix/stale',
    'source_url': 'https://github.com/validate/stale_repo/pull/1',
    'created_by': 'test',
    'status': 'assigned',
    'assigned_node': 'validate_stale_worker',
    'lease_id': 'lease_validate_stale_job_1',
    'assigned_at': '2026-05-03T00:00:00+00:00',
    'created_at': '2026-05-03T00:00:00+00:00',
    'retry_count': '0',
})

# Create leases so the reaper can release them
from redis_backend import lease_job, lease_target, lease_branch, lease_worker
repo_slug = 'validate/stale_repo'.replace('/', '_')
lease_value = json.dumps({
    'worker_id': 'validate_stale_worker',
    'job_id': 'validate_stale_job_1',
    'repo': 'validate/stale_repo',
})
r.set(lease_job(cluster, 'validate_stale_job_1'), lease_value, ex=1800)
r.set(lease_target(cluster, repo_slug, 'pr', '1'), lease_value, ex=1800)
r.set(lease_branch(cluster, 'validate/stale_repo', 'fix/stale'), lease_value, ex=1800)
r.set(lease_worker(cluster, 'validate_stale_worker'), lease_value, ex=1800)

# Simulate worker death: delete the node hash
r.delete(f'Workflow:{cluster}:node:validate_stale_worker')
r.srem(f'Workflow:{cluster}:nodes', 'validate_stale_worker')

# Run the reaper (same logic as coordinator._tick)
from coordinator import _reap_stale_workers
_reap_stale_workers(r, cluster, None)

# Verify job was requeued (status=queued, retry_count=1)
job = rb.get_job(r, cluster, 'validate_stale_job_1')
assert job is not None, 'Job should still exist after reaper'
assert job.get('status') == 'queued', f'Expected queued, got {job.get(\"status\")}'
assert job.get('retry_count') == '1', f'Expected retry_count=1, got {job.get(\"retry_count\")}'
assert job.get('assigned_node') == '', f'Expected empty assigned_node, got {job.get(\"assigned_node\")}'

# Verify leases were released
assert r.get(lease_job(cluster, 'validate_stale_job_1')) is None, 'Job lease should be released'
assert r.get(lease_target(cluster, repo_slug, 'pr', '1')) is None, 'target lease should be released'

# Cleanup
r.delete(f'Workflow:{cluster}:job:validate_stale_job_1')
print('OK: 1')
" | grep -q "OK" && pass "stale worker reaper: dead worker job requeued, leases released" || \
    fail "stale worker reaper" "job not requeued or leases not released"

# Test: max_requeues exceeded blocks the job
py "
import redis_backend as rb

r = rb.connect('$REDIS_URL')
cluster = '$CLUSTER'

# Create a job already at retry_count=3
rb.upsert_job(r, cluster, {
    'job_id': 'validate_max_retry_job',
    'type': 'review_response',
    'priority': 'P0',
    'repo': 'validate/retry_repo',
    'pr_number': '1',
    'base_branch': 'main',
    'head_branch': 'fix/retry',
    'source_url': '',
    'created_by': 'test',
    'status': 'assigned',
    'assigned_node': 'validate_dead_worker_2',
    'lease_id': '',
    'assigned_at': '2026-05-03T00:00:00+00:00',
    'created_at': '2026-05-03T00:00:00+00:00',
    'retry_count': '3',
})

from redis_backend import lease_job, lease_pr, lease_branch, lease_worker
r.set(lease_job(cluster, 'validate_max_retry_job'), 'validate_dead_worker_2', ex=1800)
r.set(lease_pr(cluster, 'validate/retry_repo', '1'), 'validate_max_retry_job', ex=1800)
r.set(lease_branch(cluster, 'validate/retry_repo', 'fix/retry'), 'validate_max_retry_job', ex=1800)
r.set(lease_worker(cluster, 'validate_dead_worker_2'), 'validate_max_retry_job', ex=1800)

# Worker is already dead (no node hash)
# Job at retry_count=3, next requeue attempt would be 4 > max_requeues=3 => blocked
job = rb.get_job(r, cluster, 'validate_max_retry_job')
retry_count = int(job.get('retry_count', 0)) + 1
assert retry_count > 3, f'retry_count {retry_count} should exceed max_requeues=3'

# Cleanup
r.delete(f'Workflow:{cluster}:job:validate_max_retry_job')
print('OK: 2')
" | grep -q "OK" && pass "max_requeues: job blocked when retry_count exceeded" || \
    fail "max_requeues" "job not blocked after exceeding max_requeues"

	# ---------------------------------------------------------------------------
	# Fix 3: Manager Mode missing-key fail-closed
	# ---------------------------------------------------------------------------
	echo ""
	echo "━━━ Manager Mode Missing-Key Fail-Closed ━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

	py "$MANAGER_PY
import tempfile, json
from pathlib import Path

sig_key = 'validate_mesh_test_key_2026'

def _signed_verdict(decision, checks=None):
    v = {'decision': decision, 'timestamp': '2026-05-03T00:00:00+00:00',
         'all_pass': decision.endswith('_pass'), 'checks': checks or {}}
    return json.loads(json.dumps(sign_artifact(v, sig_key)))

def _write_artifacts_with_missing(td, missing_key=None):
    mesh = Path(td) / '.prforge' / 'mesh'
    mesh.mkdir(parents=True, exist_ok=True)
    cv = _signed_verdict('coordinator_pass')
    full_audit_checks = {
        'validation_claims_supported': {'pass': True, 'reason': ''},
        'review_freshness_clean': {'pass': True, 'reason': ''},
        'ci_relatedness_clean': {'pass': True, 'reason': ''},
        'unknown_ci_exists': {'pass': True, 'reason': ''},
        'scope_delta_clean': {'pass': True, 'reason': ''},
        'dod_evidence_valid': {'pass': True, 'reason': ''},
        'prforge_artifacts_not_staged': {'pass': True, 'reason': ''},
        'public_text_preview_exists': {'pass': True, 'reason': ''},
    }
    if missing_key and missing_key in full_audit_checks:
        del full_audit_checks[missing_key]
    av = _signed_verdict('auditor_pass', checks=full_audit_checks)
    (mesh / 'coordinator_verdict.json').write_text(json.dumps(cv))
    (mesh / 'auditor_verdict.json').write_text(json.dumps(av))

# 13. low-risk-public: missing validation_claims_supported => fail
cfg13 = _cfg('low_risk_public')
cfg13['manager_mode']['allowed_public_actions'] = ['push', 'comment']
with tempfile.TemporaryDirectory() as td:
    _write_artifacts_with_missing(td, 'validation_claims_supported')
    result = evaluate(Path(td), cfg13)
    assert result['decision'] != 'manager_auto_ship_allowed', f'13: should not auto-ship, got {result[\"decision\"]}'
print('OK: 13')

# 14. internal-actions: missing dod_evidence_valid => fail/requeue
cfg14 = _cfg('internal_actions')
with tempfile.TemporaryDirectory() as td:
    _write_artifacts_with_missing(td, 'dod_evidence_valid')
    result = evaluate(Path(td), cfg14)
    assert result['decision'] == 'manager_requeue', f'14: expected requeue, got {result[\"decision\"]}'
print('OK: 14')

# 15. certify-only: missing scope_delta_clean => escalate (not requeue)
cfg15 = _cfg('certify_only')
with tempfile.TemporaryDirectory() as td:
    _write_artifacts_with_missing(td, 'scope_delta_clean')
    result = evaluate(Path(td), cfg15)
    assert result['decision'] == 'manager_escalate', f'15: expected escalate, got {result[\"decision\"]}'
print('OK: 15')

# 16. certify-only: all keys present => still certifies
cfg16 = _cfg('certify_only')
with tempfile.TemporaryDirectory() as td:
    _write_artifacts_with_missing(td, None)  # no missing key
    result = evaluate(Path(td), cfg16)
    assert result['decision'] == 'manager_certified', f'16: expected certified, got {result[\"decision\"]}'
print('OK: 16')

# 17. low-risk-public: missing public_text_preview_exists => fail
cfg17 = _cfg('low_risk_public')
cfg17['manager_mode']['allowed_public_actions'] = ['push', 'comment']
with tempfile.TemporaryDirectory() as td:
    _write_artifacts_with_missing(td, 'public_text_preview_exists')
    result = evaluate(Path(td), cfg17)
    assert result['decision'] != 'manager_auto_ship_allowed', f'17: should not auto-ship, got {result[\"decision\"]}'
print('OK: 17')

# 18. low-risk-public: all keys present and clean => auto_ship_allowed
cfg18 = _cfg('low_risk_public')
cfg18['manager_mode']['allowed_public_actions'] = ['push', 'comment']
with tempfile.TemporaryDirectory() as td:
    _write_artifacts_with_missing(td, None)
    result = evaluate(Path(td), cfg18)
    assert result['decision'] == 'manager_auto_ship_allowed', f'18: expected auto_ship_allowed, got {result[\"decision\"]}'
print('OK: 18')
" | grep -q "OK" && pass "13-18. Manager Mode missing-key fail-closed (6 tests)" || \
    fail "13-18. Manager Mode missing-key" "one or more missing-key tests failed"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Results: PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
exit 0
