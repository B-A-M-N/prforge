---
name: pr-rollback
description: "Revert or rollback changes from a PRForge operation."
allowed-tools: Read, Write, Bash
---

# /pr-rollback — PRForge Rollback Command

This command attempts to rollback PRForge operations to a safe state, primarily by checking Git.

## Execution
Run the following script to initiate rollback logic:
```bash
if [ -f "$HOME/prforge/scripts/mesh/fix_gaps.py" ]; then
    # Optional integration hook
    echo "Running rollback script..."
fi

echo "To manually rollback:"
echo "1. Run git status --short and identify PRForge-owned changes"
echo "2. Preserve unrelated or user-owned changes; do not discard them"
echo "3. Move unsafe distributed worktrees to quarantine instead of deleting them"
echo "4. Ask before any destructive restore, reset, clean, or branch switch"
```
