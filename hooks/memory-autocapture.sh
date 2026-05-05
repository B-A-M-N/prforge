#!/bin/bash
# PRForge Memory Autocapture Hook
# PostToolUse hook: automatically logs events and registers artifacts.
# MUST be deterministic — model never needs to "remember" to call memory_ledger.py.
# Output is silent (autocapture should not distract the model).

set +e  # Never block tool execution

HOOK_JSON=$(cat)
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"

# Logging (debug only, not shown to model)
LOG_DIR="$HOME/.prforge/hook-logs"
mkdir -p "$LOG_DIR" 2>/dev/null

log() {
    echo "$(date -Iseconds) [memory-autocapture] $1" >> "$LOG_DIR/autocapture.log" 2>/dev/null || true
}

log "Hook fired"

# Get RUN_ID from state if available
RUN_ID=""
CURRENT_PHASE=""
if [ -f ".prforge/state.json" ]; then
    RUN_ID=$(python3 -c "import json; d=json.load(open('.prforge/state.json')); print(d.get('memory_context',{}).get('memory_run_id',''))" 2>/dev/null || echo "")
    CURRENT_PHASE=$(python3 -c "import json; d=json.load(open('.prforge/state.json')); print(d.get('phase',''))" 2>/dev/null || echo "")
fi

if [ -z "$RUN_ID" ] || [ "$RUN_ID" = "None" ] || [ "$RUN_ID" = "" ]; then
    log "No RUN_ID — skipping"
    exit 0
fi

log "RUN_ID=$RUN_ID PHASE=$CURRENT_PHASE"

# Parse tool name from hook context (available as env var or need to parse)
# Claude Code sets TOOL_NAME in newer versions; fall back to parsing
TOOL_NAME=""
if [ -n "$TOOL_NAME" ]; then
    :  # use env var
else
    # Try to detect from hook JSON
    TOOL_NAME=$(echo "$HOOK_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null || echo "")
fi

log "TOOL_NAME=$TOOL_NAME"

# Handle Write tool
if echo "$TOOL_NAME" | grep -qi "write"; then
    FILE_PATH=$(echo "$HOOK_JSON" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(d.get('tool_input',{}).get('file_path',''))
" 2>/dev/null || echo "")

    if [ -z "$FILE_PATH" ] || [ ! -f "$FILE_PATH" ]; then
        log "Write: no valid file_path, skipping"
        exit 0
    fi

    # Compute sha256
    SHA=$(sha256sum "$FILE_PATH" 2>/dev/null | awk '{print $1}' || echo "")
    if [ -z "$SHA" ]; then
        log "Failed to compute sha256 for $FILE_PATH"
        exit 0
    fi

    # Determine artifact type
    ARTIFACT_TYPE="unknown"
    case "$FILE_PATH" in
        */state.json) ARTIFACT_TYPE="state" ;;
        */contract.md) ARTIFACT_TYPE="contract" ;;
        */dod.md) ARTIFACT_TYPE="dod" ;;
        */validation_ledger.md) ARTIFACT_TYPE="validation_ledger" ;;
        */review_decomposition.md) ARTIFACT_TYPE="review_decomposition" ;;
        */postmortem.json) ARTIFACT_TYPE="postmortem" ;;
        *) ARTIFACT_TYPE="file" ;;
    esac

    # Register artifact
    python3 "$PLUGIN_ROOT/scripts/memory_ledger.py" add-artifact \
        --run-id "$RUN_ID" \
        --type "$ARTIFACT_TYPE" \
        --path "$FILE_PATH" \
        --run-dir "$(pwd)/.prforge/runs/$RUN_ID" 2>/dev/null || true

    # Log event
    PAYLOAD=$(echo "{\"path\":\"$FILE_PATH\",\"sha256\":\"$SHA\"}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d))" 2>/dev/null || echo "{}")
    python3 "$PLUGIN_ROOT/scripts/memory_ledger.py" append-event \
        --run-id "$RUN_ID" \
        --phase "$CURRENT_PHASE" \
        --type "file_written" \
        --payload "$PAYLOAD" 2>/dev/null || true

    log "Registered artifact: $FILE_PATH (type=$ARTIFACT_TYPE, sha=$SHA)"

    # Special handling for state.json: detect phase transition
    case "$FILE_PATH" in
        */state.json)
            NEW_PHASE=$(python3 -c "
import json
try:
    d = json.load(open('$FILE_PATH'))
    print(d.get('phase',''))
except:
    print('')
" 2>/dev/null || echo "")
            if [ -n "$NEW_PHASE" ] && [ "$NEW_PHASE" != "$CURRENT_PHASE" ]; then
                log "Phase transition: $CURRENT_PHASE -> $NEW_PHASE"
                PAYLOAD="{\"from\":\"$CURRENT_PHASE\",\"to\":\"$NEW_PHASE\"}"
                python3 "$PLUGIN_ROOT/scripts/memory_ledger.py" append-event \
                    --run-id "$RUN_ID" \
                    --phase "$NEW_PHASE" \
                    --type "phase_transition" \
                    --payload "$PAYLOAD" 2>/dev/null || true
            fi
            ;;
    esac
fi

# Handle Edit tool (similar to Write)
if echo "$TOOL_NAME" | grep -qi "edit"; then
    FILE_PATH=$(echo "$HOOK_JSON" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(d.get('tool_input',{}).get('file_path',''))
" 2>/dev/null || echo "")

    if [ -z "$FILE_PATH" ] || [ ! -f "$FILE_PATH" ]; then
        log "Edit: no valid file_path, skipping"
        exit 0
    fi

    SHA=$(sha256sum "$FILE_PATH" 2>/dev/null | awk '{print $1}' || echo "")
    [ -z "$SHA" ] && exit 0

    python3 "$PLUGIN_ROOT/scripts/memory_ledger.py" add-artifact \
        --run-id "$RUN_ID" \
        --type "file" \
        --path "$FILE_PATH" \
        --run-dir "$(pwd)/.prforge/runs/$RUN_ID" 2>/dev/null || true

    PAYLOAD="{\"path\":\"$FILE_PATH\",\"sha256\":\"$SHA\"}"
    python3 "$PLUGIN_ROOT/scripts/memory_ledger.py" append-event \
        --run-id "$RUN_ID" \
        --phase "$CURRENT_PHASE" \
        --type "file_edited" \
        --payload "$PAYLOAD" 2>/dev/null || true

    log "Registered edit: $FILE_PATH"
fi

# Handle Bash tool
if echo "$TOOL_NAME" | grep -qi "bash"; then
    CMD=$(echo "$HOOK_JSON" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(d.get('tool_input',{}).get('command',''))
" 2>/dev/null || echo "")

    if [ -z "$CMD" ]; then
        exit 0
    fi

    # Log bash command event
    # Escape the command for JSON
    PAYLOAD=$(echo "$CMD" | python3 -c "
import json,sys
cmd = sys.stdin.read()
d = {'command': cmd.strip()}
print(json.dumps(d))
" 2>/dev/null || echo "{}")

    python3 "$PLUGIN_ROOT/scripts/memory_ledger.py" append-event \
        --run-id "$RUN_ID" \
        --phase "$CURRENT_PHASE" \
        --type "bash_command" \
        --payload "$PAYLOAD" 2>/dev/null || true

    log "Logged bash command: $CMD"

    # Special: if git commit, register the commit as artifact
    if echo "$CMD" | grep -q "^git commit"; then
        # Wait a moment for commit to complete, then capture
        sleep 1
        COMMIT_HASH=$(git rev-parse HEAD 2>/dev/null || echo "")
        if [ -n "$COMMIT_HASH" ]; then
            # Create a temp file with commit info
            TMPFILE=".prforge/runs/$RUN_ID/commits.jsonl"
            mkdir -p "$(dirname $TMPFILE)" 2>/dev/null
            echo "{\"hash\":\"$COMMIT_HASH\",\"timestamp\":\"$(date -Iseconds)\"}" >> "$TMPFILE" 2>/dev/null || true

            python3 "$PLUGIN_ROOT/scripts/memory_ledger.py" add-artifact \
                --run-id "$RUN_ID" \
                --type "commit" \
                --path "$TMPFILE" \
                --run-dir "$(pwd)/.prforge/runs/$RUN_ID" 2>/dev/null || true

            log "Registered commit: $COMMIT_HASH"
        fi
    fi
fi

log "Hook completed"
exit 0
