#!/bin/bash
# trigger-committer.sh
# PostToolUse hook that triggers a committer worker after a reviewer commits review-details.md.
#
# This hook is called after every Bash command. It checks:
# 1. If the command was a git commit
# 2. If the current agent type is "reviewer"
# 3. If review-details.md was committed
# If all conditions are met, it calls the orchestrator to spawn a committer.

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

# Check if we're a reviewer agent
AGENT_TYPE="${AGENT_TYPE:-worker}"
if [ "$AGENT_TYPE" != "reviewer" ]; then
    exit 0
fi

# Check required environment variables
if [ -z "$JOB_GROUP_ID" ] || [ -z "$AGENT_ID" ] || [ -z "$AGENT_BRANCH" ] || [ -z "$AGENT_WORKTREE_PATH" ]; then
    echo "Missing required environment variables" >&2
    exit 0
fi

# Change to the worktree directory to check for review-details.md
cd "$AGENT_WORKTREE_PATH" 2>/dev/null || exit 0

# Check if review-details.md was part of the last commit
COMMITTED_FILES=$(git diff-tree --no-commit-id --name-only -r HEAD 2>/dev/null || echo "")
if ! echo "$COMMITTED_FILES" | grep -q "review-details.md"; then
    echo "[Hook] review-details.md not in commit, skipping committer trigger" >&2
    exit 0
fi

# Orchestrator service URL (K8s internal DNS)
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://orchestrator.backend.svc.cluster.local:8000}"

echo "[Hook] Reviewer committed review-details.md. Triggering committer..." >&2

# Call orchestrator to spawn a committer
RESPONSE=$(curl -s -X POST "${ORCHESTRATOR_URL}/api/spawn-worker" \
    -H "Content-Type: application/json" \
    -d "{
        \"group_id\": \"${JOB_GROUP_ID}\",
        \"agent_id\": ${AGENT_ID},
        \"branch\": \"${AGENT_BRANCH}\",
        \"worktree_path\": \"${AGENT_WORKTREE_PATH}\",
        \"agent_type\": \"committer\",
        \"prompt\": \"Merge the reviewed branch to main.\"
    }" 2>&1)

if [ $? -eq 0 ]; then
    echo "[Hook] Committer spawned successfully: $RESPONSE" >&2
else
    echo "[Hook] Failed to spawn committer: $RESPONSE" >&2
fi

# Return success - don't block the commit
exit 0
