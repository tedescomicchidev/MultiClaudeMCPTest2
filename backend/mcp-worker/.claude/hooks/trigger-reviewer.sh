#!/bin/bash
# trigger-reviewer.sh
# PostToolUse hook that triggers a reviewer mcp-worker when a worker commits changes.
#
# This hook fires after successful Bash tool execution. It checks if:
#   1. The current agent is a "worker" type (not reviewer or committer)
#   2. The command was a git commit
#
# If both conditions are met, it spawns a new reviewer mcp-worker.

set -e

# Read hook input from stdin
INPUT=$(cat)

# Extract relevant fields
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')

# Only process Bash tool calls
if [ "$TOOL_NAME" != "Bash" ]; then
    exit 0
fi

# Check if this is a git commit command
if ! echo "$COMMAND" | grep -q 'git commit'; then
    exit 0
fi

# Get the current agent type from environment
CURRENT_AGENT_TYPE="${AGENT_TYPE:-worker}"

# Only trigger reviewer for worker agents (not for reviewers or committers)
if [ "$CURRENT_AGENT_TYPE" != "worker" ]; then
    exit 0
fi

# Get branch and worktree info from environment
BRANCH="${AGENT_BRANCH:-}"
WORKTREE="${AGENT_WORKTREE_PATH:-$CWD}"
AGENT_ID="${AGENT_ID:-0}"
GROUP_ID="${JOB_GROUP_ID:-unknown}"

# If no branch is set, we can't trigger a reviewer
if [ -z "$BRANCH" ]; then
    echo "No branch set, skipping reviewer trigger" >&2
    exit 0
fi

# Generate a new agent ID for the reviewer
REVIEWER_ID="${AGENT_ID}-reviewer"

echo "Worker agent $AGENT_ID committed to branch $BRANCH. Triggering reviewer..." >&2

# Spawn the reviewer worker
# This uses the same Python script but with AGENT_TYPE=reviewer
# The worker.py will handle the reviewer-specific system prompt

# Export environment variables for the new worker
export AGENT_TYPE="reviewer"
export AGENT_ID="$REVIEWER_ID"
export JOB_GROUP_ID="$GROUP_ID"
export AGENT_BRANCH="$BRANCH"
export AGENT_WORKTREE_PATH="$WORKTREE"
export AGENT_PROMPT="Review the latest commit on branch $BRANCH."

# Run the reviewer worker in the background
# Using nohup to ensure it continues even if the parent process exits
nohup python3 /app/worker.py > "/tmp/reviewer-${REVIEWER_ID}.log" 2>&1 &

echo "Reviewer worker $REVIEWER_ID started for branch $BRANCH" >&2

# Return success with additional context for Claude
jq -n '{
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "A reviewer agent has been automatically spawned to review your commit."
    }
}'

exit 0
