#!/bin/bash
# trigger-reviewer.sh
# PostToolUse hook that triggers a reviewer worker after a worker agent commits.
#
# This hook is called after every Bash command. It checks:
# 1. If the command was a git commit
# 2. If the current agent type is "worker"
# If both conditions are met, it calls the orchestrator to spawn a reviewer.

set -e

# Read hook input from stdin
INPUT=$(cat)

# Extract tool information
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Only proceed if this is a Bash tool call with a git commit command
if [ "$TOOL_NAME" != "Bash" ]; then
    exit 0
fi

# Check if the command is a git commit
if ! echo "$COMMAND" | grep -qE '^git\s+commit|&&\s*git\s+commit'; then
    exit 0
fi

# Check if we're a worker agent (not reviewer or committer)
AGENT_TYPE="${AGENT_TYPE:-worker}"
if [ "$AGENT_TYPE" != "worker" ]; then
    exit 0
fi

# Check required environment variables
if [ -z "$JOB_GROUP_ID" ] || [ -z "$AGENT_ID" ] || [ -z "$AGENT_BRANCH" ] || [ -z "$AGENT_WORKTREE_PATH" ]; then
    echo "Missing required environment variables" >&2
    exit 0
fi

# Orchestrator service URL (K8s internal DNS)
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://orchestrator.backend.svc.cluster.local:8000}"

echo "[Hook] Worker agent committed. Triggering reviewer..." >&2

# Call orchestrator to spawn a reviewer
RESPONSE=$(curl -s -X POST "${ORCHESTRATOR_URL}/api/spawn-worker" \
    -H "Content-Type: application/json" \
    -d "{
        \"group_id\": \"${JOB_GROUP_ID}\",
        \"agent_id\": ${AGENT_ID},
        \"branch\": \"${AGENT_BRANCH}\",
        \"worktree_path\": \"${AGENT_WORKTREE_PATH}\",
        \"agent_type\": \"reviewer\",
        \"prompt\": \"Review the latest commit on this branch.\"
    }" 2>&1)

if [ $? -eq 0 ]; then
    echo "[Hook] Reviewer spawned successfully: $RESPONSE" >&2
else
    echo "[Hook] Failed to spawn reviewer: $RESPONSE" >&2
fi

# Return success - don't block the commit
exit 0
