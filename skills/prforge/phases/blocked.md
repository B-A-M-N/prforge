# BLOCKED State

Read this file when any phase hits a blocker.

---

## BLOCKED State

If you hit a blocker at any phase:

1. Update `state.json` phase to `BLOCKED`
2. Write the blocker to `state.json` `blocker` field
3. Present to user:

```
# Blocked

## What failed
[One sentence]

## What I think is wrong
[1-2 sentences]

## Next action
[I can continue by doing X]

Options: [Continue fixing] [Stop] [Show details]
```

Do not dump 400 lines of logs. Summarize. Put details in artifacts.

---

## Git Disaster Recovery

If the user asks `/pr recover` or if you detect git state problems during INTAKE:

```bash
git status --short
git branch -vv
git remote -v
git fetch --all --prune
git log --oneline --decorate --graph --all -20
```

Classify the problem:
- Branch behind remote
- Branch diverged (local and remote have different commits)
- Wrong remote (tracking upstream instead of fork)
- Remote branch already exists (stale previous attempt)
- Local branch based on wrong base
- Detached HEAD
- Uncommitted changes that don't belong to this PR
- Accidental edits on main

Recommend exact commands. Example:

```
Diagnosis: Your local branch and origin/fix/foo diverged. The remote branch
appears to be an older failed attempt from your fork.

Safe recovery:
git push --force-with-lease origin fix/foo

This is safe because: [reason]
```
