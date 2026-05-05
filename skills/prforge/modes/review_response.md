# Mode: review_response — Additional Instructions

Read this file immediately after detecting task type `review_response` in INTAKE.
This file supplements the phase playbooks — it does not replace them.

---

## When This Mode Activates

- User pastes their own GitHub PR link AND `gh` detects review comments on that PR
- User pastes a review link or review comment directly
- User says "address requested changes", "handle this review", "respond to maintainer feedback"

**Implicit trigger:** If the user pastes a GitHub PR link and `gh` detects review
comments on that PR, automatically activate in `review_response` mode. Do NOT wait
for an explicit command.

---

## Mode-Specific INVESTIGATE Requirements

The mandatory review collection pass (Steps 1–5) defined in `phases/investigate.md`
is REQUIRED in this mode. It is not optional or partial. Summarized here for
emphasis — full instructions are in investigate.md:

- Fetch ALL review data: inline comments, general reviews, PR threads, CI checks
- Classify EVERY concern using the classification table (blocker / required_change / maintainer_preference / scope_reduction / optional_suggestion / misunderstanding / needs_user_decision / already_addressed)
- Record as `review_decomposition.md` with task queue
- Fetch CI/check status and classify as related/unrelated
- Record `state.review_freshness.last_fetched_at`

**If you cannot fetch review data** (API errors, private repo, etc.), stop and tell
the user. Do not proceed with a partial understanding of the feedback.

### `needs_user_decision` handling

Comments classified as `needs_user_decision` MUST NOT be auto-fixed. They must:
1. Be left as-is in the code (no automatic change)
2. Appear in `review_decomposition.md` under "Needs Your Decision"
3. Appear in `approval.md` under a prominent "Needs Your Decision" section
4. Include: original comment text, plain-English interpretation of what the maintainer seems to be suggesting, and why it wasn't auto-fixed

Examples that require `needs_user_decision`:
- "Maybe we should support this differently?"
- "Should this be configurable?"
- "Can we align this with the new architecture?"

---

## Mode-Specific IMPLEMENT Rules

- Address EVERY required item from `review_decomposition.md` — ALL of them, no exceptions
- Remove any changes the maintainer flagged as out of scope (scope_reduction items)
- Add tests for any edge cases the maintainer called out
- Before leaving IMPLEMENT, verify every required item in the task queue is marked complete
- `needs_user_decision` items are NOT addressed in code — they are surfaced in approval.md

---

## Mode-Specific PACKAGE Requirements

Generate `review_response.md` in addition to (or instead of) `pr_body.md`.

**CRITICAL: `review_response.md` is an INTERNAL DRAFT. Never post it directly.**
- The "# Maintainer Response Draft" heading is for internal use only
- When posting the final comment, strip the heading — post ONLY the body text
- The review response MUST go through the APPROVAL gate before posting
- Include the exact posted text in `approval.md` → `state.public_text.review_response`

**Template for `review_response.md` (internal draft):**
```markdown
# Maintainer Response Draft  ← INTERNAL ONLY, strip before posting

**Commit:** `<full-sha>` (`<short-sha>`)

Thanks, acknowledged. [One sentence acknowledging each concern.]

[One to two sentences explaining what you changed and why.]

Validation:
- `<test command>` — passed
- `<typecheck command>` — passed
```

**When posting the final comment to GitHub, use ONLY this format:**
```markdown
Thanks, acknowledged. [One sentence.]

[One to two sentences explaining what changed and why.]

Validation:
- `<test command>` — passed
- `<typecheck command>` — passed
```

Do NOT include "# Maintainer Response Draft" in the posted text.
Do NOT post until the user approves via the APPROVAL gate.

**Tone rules:**
- No defensiveness
- No "the AI did it"
- No over-explaining
- No arguing unless strong evidence
- Always acknowledge valid concern first
- Be direct and professional

---

## Mode-Specific approval.md Requirements

The approval artifact MUST include a prominent section:

```markdown
## Needs Your Decision

The following reviewer comments were NOT auto-addressed because they require
a product or architecture decision:

### D1 — [Short description]
- **Reviewer:** @reviewer_login
- **Original comment:** "[Exact or paraphrased quote]"
- **What they seem to be suggesting:** [Plain-English interpretation]
- **Why this wasn't auto-fixed:** [Ambiguous intent / requires product decision / etc.]
- **Your options:** [Option A] / [Option B] / [Defer to next PR]
```

If there are no `needs_user_decision` items, omit this section.

---

## Schema Requirements for This Mode

At PLAN time, read and merge `$SKILL_ROOT/schemas/review_response.json` with
`$SKILL_ROOT/schemas/base.json`. Additional required fields:

- `required_at_investigate`: `review_decomposition`, `review_freshness`
- `required_at_implement`: `completed_reviews`

Verify `state.json` has all these fields before leaving each phase.
