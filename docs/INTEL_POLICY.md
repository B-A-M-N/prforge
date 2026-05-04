# PRForge Intel + Policy Architecture

PRForge uses intelligence in two paths:

```text
Information path:
  embeddings/retrieval/reranking select context for the agent.

Enforcement path:
  deterministic gates plus intel risk signals choose allow, warning,
  recoverable redirect, or escalation.
```

Intel never bypasses deterministic safety. It can only increase caution, select a
repair path, or supply context.

## Enforcement Order

```text
current event
  -> deterministic extraction
  -> local/mesh intel retrieval
  -> risk signal ranking
  -> policy decision
  -> allow / warn / redirect / escalate
```

Hard safety remains deterministic:

- public actions require approval artifacts, hash checks, allowed action checks,
  correct role, and explicit user approval
- PRForge artifacts must not be staged or tracked
- phase transitions must follow the state machine
- local worker decisions apply only inside the active run's policy bundle

## Local And Mesh Split

```text
Mesh intel = global brain
Local intel = worker survival brain
```

Mesh-level intel owns:

- cross-PR memory
- global artifact index
- audit prioritization
- queue scoring
- coordinator and auditor policy decisions

Local worker intel owns:

- current run context
- cached policy bundle
- current contract, patch plan, DoD, validation ledger
- local risk signals
- fast recoverable redirects inside the active capability envelope

## Artifact Locations

Global optional index:

```text
~/.prforge-intel/
  index/
  metadata.sqlite
  vectors/
  artifacts.jsonl
  models/
```

FastEmbed is the default local embedding/rerank implementation. It runs on CPU
and local RAM. Preflight validates the Python package and model load before the
policy engine depends on it.

Local run cache:

```text
~/.prforge/runs/<repo>/<branch-or-pr>/<run-id>/
  policy_bundle.json
  intel_context.md
  intel/
    risk_signals.json
    mesh_risk_signals.json
    local_context.md
  policy/
    last_decision.json
```

No intel index is stored in the target repo. The repo may contain only an ignored
plain `.prforge-run` pointer.

## Fail-Safe Behavior

```text
mesh intel down:
  local adaptive enforcement continues from cached policy bundle

local intel down:
  deterministic gates continue; ambiguous cases escalate or redirect

Redis down:
  current safe local work can continue until token expiry;
  risky transitions wait for reconnect

embedding/reranker down:
  adaptive enforcement is disabled; deterministic gates remain
```

## Policy CLI

Run intel preflight during setup or before starting workers:

```bash
python3 scripts/mesh/prforge_mesh.py intel-preflight --require-ready
```

This writes:

```text
~/.prforge-intel/capabilities.json
```

To build or refresh the local run index:

```bash
python3 scripts/mesh/prforge_mesh.py intel-index \
  --run-dir ~/.prforge/runs/org__repo/pr-456/run-abc
```

To retrieve and rerank risk context directly:

```bash
python3 scripts/mesh/prforge_mesh.py intel-query \
  --run-dir ~/.prforge/runs/org__repo/pr-456/run-abc \
  --query "Which prior artifacts predict missing validation for this parser change?"
```

Use the policy engine through the mesh entry point:

```bash
python3 scripts/mesh/prforge_mesh.py policy-check \
  --event phase_exit \
  --phase IMPLEMENT \
  --run-dir ~/.prforge/runs/org__repo/pr-456/run-abc \
  --repo /path/to/repo \
  --write
```

Decision shape:

```json
{
  "decision": "redirect_recoverable",
  "redirect_state": "VALIDATION_REPAIR",
  "reason": "missing_regression_test",
  "required_next_action": "Add or verify regression test for malformed parser input.",
  "intel": {
    "top_risks": []
  },
  "fail_safe": {
    "deterministic_gates_remain_active": true,
    "intel_may_bypass_public_actions": false
  }
}
```

Hooks consume `redirect_recoverable` as a detour, not task failure.

`policy-check` automatically calls the FastEmbed intel engine when
`capabilities.json` says preflight passed. Embeddings retrieve broad candidate
artifacts; the reranker selects the most relevant risk context; policy consumes
the resulting `intel/risk_signals.json`.
