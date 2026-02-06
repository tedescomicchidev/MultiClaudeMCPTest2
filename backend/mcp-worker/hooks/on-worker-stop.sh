#!/bin/bash
# on-worker-stop.sh
#
# Stop hook for the "worker" role.
# Fires when the worker agent finishes responding.
# If the worker committed changes on its branch, this hook calls
# the orchestrator to spawn a "reviewer" agent for that branch.

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
: "${AGENT_ID:?}"

# Only trigger the reviewer if the worker actually committed something.
cd "$AGENT_WORKTREE_PATH" || exit 0
COMMIT_COUNT=$(git log --oneline main.. 2>/dev/null | wc -l)
if [ "$COMMIT_COUNT" -eq 0 ]; then
  exit 0
fi

# Call the orchestrator to start a reviewer job for this branch.
curl -sf -X POST \
  "http://orchestrator.backend.svc.cluster.local:8080/api/start-reviewer" \
  -H "Content-Type: application/json" \
  -d "{
    \"worktree_path\": \"$AGENT_WORKTREE_PATH\",
    \"branch\": \"$AGENT_BRANCH\",
    \"group_id\": \"$JOB_GROUP_ID\",
    \"agent_id\": \"$AGENT_ID\"
  }" >/dev/null 2>&1 || true

exit 0
