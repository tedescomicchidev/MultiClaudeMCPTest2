#!/bin/bash
# trigger-committer.sh
# PostToolUse hook that triggers a committer mcp-worker when a reviewer commits review-details.md.
#
# This hook fires after successful Bash tool execution. It checks if:
#   1. The current agent is a "reviewer" type
#   2. The command was a git commit
#   3. The commit includes review-details.md
#
# If all conditions are met, it spawns a new committer mcp-worker.

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

# Only trigger committer for reviewer agents
if [ "$CURRENT_AGENT_TYPE" != "reviewer" ]; then
    exit 0
fi

# Get branch and worktree info from environment
BRANCH="${AGENT_BRANCH:-}"
WORKTREE="${AGENT_WORKTREE_PATH:-$CWD}"
AGENT_ID="${AGENT_ID:-0}"
GROUP_ID="${JOB_GROUP_ID:-unknown}"

# If no branch is set, we can't trigger a committer
if [ -z "$BRANCH" ]; then
    echo "No branch set, skipping committer trigger" >&2
    exit 0
fi

# Check if review-details.md was staged/committed
# We check the git status to see if review-details.md was part of the commit
cd "$WORKTREE" 2>/dev/null || cd "$CWD" 2>/dev/null || exit 0

# Check if review-details.md exists in the repository
if [ ! -f "review-details.md" ]; then
    echo "review-details.md not found, skipping committer trigger" >&2
    exit 0
fi

# Check if review-details.md was part of the last commit
if ! git show --name-only --pretty=format: HEAD 2>/dev/null | grep -q "review-details.md"; then
    echo "review-details.md not in latest commit, skipping committer trigger" >&2
    exit 0
fi

# Generate a new agent ID for the committer
COMMITTER_ID="${AGENT_ID%-reviewer}-committer"

echo "Reviewer agent $AGENT_ID committed review-details.md. Triggering committer..." >&2

# Spawn the committer worker
# This uses the same Python script but with AGENT_TYPE=committer
# The worker.py will handle the committer-specific system prompt

# Export environment variables for the new worker
export AGENT_TYPE="committer"
export AGENT_ID="$COMMITTER_ID"
export JOB_GROUP_ID="$GROUP_ID"
export AGENT_BRANCH="$BRANCH"
export AGENT_WORKTREE_PATH="$WORKTREE"
export AGENT_PROMPT="Merge branch $BRANCH to main after verifying the review."

# Run the committer worker in the background
# Using nohup to ensure it continues even if the parent process exits
nohup python3 /app/worker.py > "/tmp/committer-${COMMITTER_ID}.log" 2>&1 &

echo "Committer worker $COMMITTER_ID started for branch $BRANCH" >&2

# Return success with additional context for Claude
jq -n '{
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "A committer agent has been automatically spawned to merge the reviewed changes."
    }
}'

exit 0
