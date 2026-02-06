#!/bin/bash
# on-reviewer-stop.sh
#
# Stop hook for the "reviewer" role.
# Fires when the reviewer agent finishes responding.
# If the reviewer committed review-details.md on the branch, this hook
# calls the orchestrator to spawn a "committer" agent that merges the
# branch back into main.

set -euo pipefail

INPUT=$(cat)

# Guard: if the Stop hook already triggered a continuation, let Claude stop.
if [ "$(echo "$INPUT" | jq -r '.stop_hook_active')" = "true" ]; then
  exit 0
fi

# Check that the required env vars are set (set by the K8s Job)
: "${AGENT_WORKTREE_PATH:?}"
: "${AGENT_BRANCH:?}"
: "${JOB_GROUP_ID:?}"

# Only trigger the committer if review-details.md was committed on this branch.
cd "$AGENT_WORKTREE_PATH" || exit 0
if ! git log --name-only --oneline main.. 2>/dev/null | grep -q "review-details.md"; then
  exit 0
fi

# Call the orchestrator to start a committer job for this branch.
curl -sf -X POST \
  "http://orchestrator.backend.svc.cluster.local:8080/api/start-committer" \
  -H "Content-Type: application/json" \
  -d "{
    \"worktree_path\": \"$AGENT_WORKTREE_PATH\",
    \"branch\": \"$AGENT_BRANCH\",
    \"group_id\": \"$JOB_GROUP_ID\"
  }" >/dev/null 2>&1 || true

exit 0
