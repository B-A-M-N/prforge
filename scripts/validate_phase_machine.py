#!/usr/bin/env python3
"""PRForge Phase Consistency Validator

Checks that the phase machine is coherent across all definition points:
- SKILL.md phase list and transitions
- phase-boundary.sh transition table
- phase-injector.sh phase map
- Phase playbook files (phases/<lowercase>.md)
- README.md phase list

Run: python3 scripts/validate_phase_machine.py
"""

import os
import re
import sys
from pathlib import Path

# ── Canonical definition ──────────────────────────────────────────────────────

CANONICAL_PHASES = [
    "INTAKE",
    "INVESTIGATE",
    "PLAN",
    "IMPLEMENT",
    "VALIDATE",
    "SELF_REVIEW",
    "PACKAGE",
    "APPROVAL",
    "POSTMORTEM",
    "MEMORY_INDEX",
    "COMPLETE",
]

# Phases that must have a playbook file (non-terminal execution phases)
PLAYBOOK_REQUIRED = [
    "INTAKE",
    "INVESTIGATE",
    "PLAN",
    "IMPLEMENT",
    "VALIDATE",
    "SELF_REVIEW",
    "PACKAGE",
    "APPROVAL",
    "POSTMORTEM",
    "MEMORY_INDEX",
    "SHIPPED",        # legacy/outcome playbook, not a canonical phase
    "BLOCKED",
]

# Forward transitions that must exist in the boundary hook
REQUIRED_FORWARD_TRANSITIONS = [
    "INTAKE:INVESTIGATE",
    "INVESTIGATE:PLAN",
    "PLAN:IMPLEMENT",
    "IMPLEMENT:VALIDATE",
    "VALIDATE:SELF_REVIEW",
    "SELF_REVIEW:PACKAGE",
    "PACKAGE:APPROVAL",
    "APPROVAL:POSTMORTEM",
    "POSTMORTEM:MEMORY_INDEX",
    "MEMORY_INDEX:COMPLETE",
]

# Corrective transitions that must exist
REQUIRED_CORRECTIVE_TRANSITIONS = [
    "VALIDATE:IMPLEMENT",
    "SELF_REVIEW:IMPLEMENT",
    "PACKAGE:INVESTIGATE",
    "APPROVAL:PACKAGE",
    "APPROVAL:INVESTIGATE",
]

REPAIR_STATES = [
    "SCOPE_RECONCILE",
    "STATE_SYNC_REPAIR",
    "LEASE_RENEWAL_REPAIR",
    "REVIEW_REFRESH",
    "SCOPE_UPDATE",
    "PLAN_UPDATE",
    "VALIDATION_REPAIR",
    "INTELLIGENCE_REPAIR",
    "ARTIFACT_REPAIR",
    "COORDINATOR_RECONCILE",
    "STYLE_REPAIR",
    "COMMIT_REPAIR",
    "POLL_CI",
]

ROOT = Path(__file__).parent.parent
SKILL_ROOT = ROOT / "skills" / "prforge"
HOOKS_DIR = ROOT / "hooks"
PHASES_DIR = SKILL_ROOT / "phases"
README = ROOT / "README.md"


def read_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(errors="replace")


def check_skill_md():
    """Extract phase list and transitions from SKILL.md."""
    content = read_file(SKILL_ROOT / "SKILL.md")
    errors = []

    # Check for forbidden phase names
    for forbidden in ("CONTRACT", "REPRODUCE"):
        # Match as whole words, not inside other words like "contract.md"
        pattern = r'\b' + forbidden + r'\b'
        matches = list(re.finditer(pattern, content))
        for m in matches:
            # Skip occurrences in code comments about the old names
            line_num = content[:m.start()].count('\n') + 1
            line = content.split('\n')[line_num - 1].strip()
            if 'removed' in line.lower() or 'rename' in line.lower() or 'old' in line.lower():
                continue
            errors.append(f"SKILL.md line {line_num}: forbidden phase name '{forbidden}' in: {line}")

    # Check canonical phases appear in state machine diagram
    for phase in CANONICAL_PHASES:
        if phase not in content:
            errors.append(f"SKILL.md: canonical phase '{phase}' not found")

    # Check phase table — extract from the markdown table after "## State Machine"
    state_machine_section = content.split("## State Machine")[1] if "## State Machine" in content else content
    # Find the phase table (between | Phase | header and next ---)
    table_match = re.search(r'\| Phase \|.*?\n\|[-| ]+\|\n((?:\|.*?\n)*)', state_machine_section)
    if table_match:
        table_text = table_match.group(1)
        found_phases = re.findall(r'\*\*([A-Z_]+)\*\*', table_text)
        for phase in CANONICAL_PHASES:
            if phase not in found_phases:
                errors.append(f"SKILL.md phase table: missing canonical phase '{phase}'")
    else:
        errors.append("SKILL.md: could not find phase table after ## State Machine")

    return errors


def check_phase_boundary_sh():
    """Extract and validate transition table from phase-boundary.sh."""
    content = read_file(HOOKS_DIR / "phase-boundary.sh")
    errors = []

    # Extract all transitions from ALLOWED_TRANSITIONS array
    transitions = re.findall(r'"([A-Z_]+):([A-Z_]+)"', content)

    # Check required forward transitions
    for req in REQUIRED_FORWARD_TRANSITIONS:
        if req not in [f"{a}:{b}" for a, b in transitions]:
            errors.append(f"phase-boundary.sh: missing required forward transition '{req}'")

    # Check required corrective transitions
    for req in REQUIRED_CORRECTIVE_TRANSITIONS:
        if req not in [f"{a}:{b}" for a, b in transitions]:
            errors.append(f"phase-boundary.sh: missing required corrective transition '{req}'")

    # Check repair states are in the REPAIR_STATES array
    for state in REPAIR_STATES:
        if f'"{state}"' not in content:
            errors.append(f"phase-boundary.sh: repair state '{state}' not in REPAIR_STATES array")

    # Check no forbidden phase names
    for forbidden in ("CONTRACT_UPDATE",):
        if forbidden in content:
            errors.append(f"phase-boundary.sh: contains forbidden name '{forbidden}' (should be SCOPE_UPDATE)")

    return errors


def check_phase_injector_sh():
    """Validate phase-injector.sh maps all canonical phases."""
    content = read_file(HOOKS_DIR / "phase-injector.sh")
    errors = []

    # Extract phase names from the case statement
    case_phases = re.findall(r'^\s+([A-Z_]+(?:\|[A-Z_]+)*)\)', content, re.MULTILINE)

    # Flatten pipe-separated groups
    mapped = set()
    for group in case_phases:
        for p in group.split('|'):
            mapped.add(p.strip())

    # Terminal/post-execution phases don't need injector mappings
    INJECTOR_REQUIRED = set(CANONICAL_PHASES) - {"POSTMORTEM", "MEMORY_INDEX", "COMPLETE"}
    for phase in INJECTOR_REQUIRED:
        if phase not in mapped:
            errors.append(f"phase-injector.sh: canonical phase '{phase}' not in case mapping")

    # Check no forbidden names
    for forbidden in ("CONTRACT_UPDATE",):
        if forbidden in content:
            errors.append(f"phase-injector.sh: contains forbidden name '{forbidden}'")

    return errors


def check_playbooks():
    """Check that every required phase has a playbook file."""
    errors = []
    for phase in PLAYBOOK_REQUIRED:
        playbook = PHASES_DIR / f"{phase.lower()}.md"
        if not playbook.exists():
            errors.append(f"Missing playbook: phases/{phase.lower()}.md")

    # Check no stale playbook files for removed phases
    for stale in ("contract.md", "reproduce.md"):
        stale_path = PHASES_DIR / stale
        if stale_path.exists():
            errors.append(f"Stale playbook file exists: phases/{stale} (should be removed)")

    return errors


def check_readme():
    """Check README phase references."""
    content = read_file(README)
    errors = []

    # Check for forbidden phase names (not in changelog context)
    lines = content.split('\n')
    for i, line in enumerate(lines, 1):
        for forbidden in ("CONTRACT", "REPRODUCE"):
            if re.search(r'\b' + forbidden + r'\b', line):
                # Skip version changelog lines
                if line.strip().startswith('v') and ('removed' in line.lower() or 'rename' in line.lower() or 'old' in line.lower()):
                    continue
                # Skip lines that reference the old names descriptively
                if 'previously' in line.lower() or 'was' in line.lower() or 'formerly' in line.lower():
                    continue
                errors.append(f"README.md line {i}: forbidden phase name '{forbidden}' in: {line.strip()}")

    return errors


def main():
    all_errors = []

    checks = [
        ("SKILL.md", check_skill_md),
        ("phase-boundary.sh", check_phase_boundary_sh),
        ("phase-injector.sh", check_phase_injector_sh),
        ("Phase playbooks", check_playbooks),
        ("README.md", check_readme),
    ]

    for name, check_fn in checks:
        errors = check_fn()
        if errors:
            all_errors.extend(errors)
            print(f"  ✗ {name}: {len(errors)} issue(s)")
            for e in errors:
                print(f"    - {e}")
        else:
            print(f"  ✓ {name}: OK")

    print()
    if all_errors:
        print(f"FAIL — {len(all_errors)} phase consistency issue(s) found")
        sys.exit(1)
    else:
        print("PASS — Phase machine is consistent across all definition points")
        sys.exit(0)


if __name__ == "__main__":
    main()
