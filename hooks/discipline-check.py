#!/usr/bin/env python3
"""PRForge Discipline Check — deterministic hook logic."""
import json, os, sys, subprocess, re
import argparse
from pathlib import Path

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--harness', required=True)
    p.add_argument('--contract', required=True)
    p.add_argument('--patch-plan', required=True)
    p.add_argument('--report', required=True)
    return p.parse_args()

def read_contract_allowed(contract_path):
    """Extract allowed files from contract.md."""
    if not os.path.isfile(contract_path):
        return []
    content = Path(contract_path).read_text(errors='replace')
    # Look for ## Allowed Changes section
    m = re.search(r'## Allowed Changes\s*\n(.*?)(?=\n##|\Z)', content, re.DOTALL)
    if not m:
        return []
    lines = [l.strip().lstrip('- ').strip() for l in m.group(1).splitlines() if l.strip()]
    return [l for l in lines if l]

def get_changed_files(repo_root):
    """Get files changed in working tree."""
    result = subprocess.run(['git', 'diff', '--name-only'], capture_output=True, text=True, cwd=repo_root)
    changed = result.stdout.strip().splitlines() if result.stdout.strip() else []
    result2 = subprocess.run(['git', 'diff', '--cached', '--name-only'], capture_output=True, text=True, cwd=repo_root)
    staged = result2.stdout.strip().splitlines() if result2.stdout.strip() else []
    return list(set(changed + staged))

def check_scope_delta(changed_files, allowed_files):
    """Check if changed files are in allowed list."""
    unexpected = []
    for f in changed_files:
        matched = any(a in f or f in a for a in allowed_files if a)
        if not matched and f:
            unexpected.append(f)
    return unexpected

def check_diff_size(repo_root, threshold=10):
    """Count total files changed vs base."""
    result = subprocess.run(['git', 'diff', '--stat'], capture_output=True, text=True, cwd=repo_root)
    lines = [l for l in result.stdout.strip().splitlines() if '|' in l]
    return len(lines)

def check_dependency_touch(changed_files):
    """Flag package.json, requirements.txt, etc."""
    dep_patterns = ['package.json', 'requirements.txt', 'Pipfile', 'poetry.lock', 'Cargo.toml', 'go.mod']
    touched = [f for f in changed_files if any(p in f for p in dep_patterns)]
    return touched

def check_tests_included(changed_files):
    """Check if tests were included when source files were changed."""
    src_patterns = ['.py', '.js', '.ts', '.go', '.java', '.cpp', '.c', '.h', '.rb', '.rs']
    test_patterns = ['test', 'spec']
    
    src_changed = any(any(f.endswith(ext) for ext in src_patterns) and not any(tp in f.lower() for tp in test_patterns) for f in changed_files)
    test_changed = any(any(tp in f.lower() for tp in test_patterns) for f in changed_files)
    
    # If source files were changed but no tests were changed
    if src_changed and not test_changed:
        return False
    return True

def main():
    args = parse_args()
    report = {"status": "PASS", "phase": "IMPLEMENT", "checks": {}, "findings": [], "required_recovery": ""}

    changed = get_changed_files(args.repo)
    allowed = read_contract_allowed(args.contract)

    # Check 1: Files within contract
    if allowed:
        unexpected = check_scope_delta(changed, allowed)
        report['checks']['files_within_contract'] = len(unexpected) == 0
        if unexpected:
            report['findings'].append({
                "severity": "blocker",
                "type": "scope_delta",
                "message": f"Files edited outside contract: {', '.join(unexpected[:3])}"
            })
    else:
        report['checks']['files_within_contract'] = True  # No contract yet, skip

    # Check 2: Diff size
    diff_count = check_diff_size(args.repo)
    report['checks']['diff_size_acceptable'] = diff_count <= 10
    if diff_count > 10:
        report['findings'].append({
            "severity": "warning",
            "type": "diff_size",
            "message": f"Diff touches {diff_count} files — verify scope is justified."
        })

    # Check 3: Dependency files
    dep_touched = check_dependency_touch(changed)
    report['checks']['dependency_files_touched'] = len(dep_touched) == 0
    if dep_touched:
        report['findings'].append({
            "severity": "blocker",
            "type": "dependency_touch",
            "message": f"Dependency files touched without contract approval: {', '.join(dep_touched)}"
        })

    # Check 4: Test files
    tests_included = check_tests_included(changed)
    report['checks']['test_files_included'] = tests_included
    if not tests_included:
        report['findings'].append({
            "severity": "warning",
            "type": "missing_tests",
            "message": "Source files were modified but no test files were included. Verify test coverage."
        })

    # Determine status
    blockers = [f for f in report['findings'] if f['severity'] == 'blocker']
    if blockers:
        report['status'] = "BLOCKED"
        report['required_recovery'] = "Revert unexpected files or update contract.md, patch_plan.md, and dod.md before continuing."
    elif report['findings']:
        report['status'] = "WARNING"

    Path(args.report).write_text(json.dumps(report, indent=2), encoding='utf-8')
    sys.exit(0)

if __name__ == '__main__':
    main()
