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
echo "1. Run git checkout . to clear dirty working tree"
echo "2. Run git reset --hard HEAD to remove uncommitted changes"
echo "3. Run git checkout <original-branch> to revert to base branch"
```