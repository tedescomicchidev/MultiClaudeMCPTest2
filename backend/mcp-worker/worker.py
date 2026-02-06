"""MCP Worker: runs a Claude Agent SDK query with 'claude mcp serve' as the STDIO MCP server.

Supports three roles (set via AGENT_ROLE env var):

  worker    – executes the user's prompt, commits results to its branch.
              A Stop hook triggers a "reviewer" agent when the worker commits.
  reviewer  – reviews the worker's commits, creates review-details.md,
              commits the review.  A Stop hook triggers a "committer" agent
              when review-details.md is committed.
  committer – merges the agent branch back into main.

Env vars consumed:
  AGENT_PROMPT          : the user's prompt (worker/reviewer use it)
  AGENT_ID              : numeric agent identifier
  JOB_GROUP_ID          : run group identifier
  AGENT_BRANCH          : git branch assigned to this agent
  AGENT_WORKTREE_PATH   : path to the git worktree (or repo dir for committer)
  AGENT_ROLE            : worker | reviewer | committer  (default: worker)
  REPO_DIR              : path to the main git repo (used by committer)
"""

import asyncio
import json
import os
import sys

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    ResultMessage,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
)

# ── Hook settings generators ─────────────────────────────────────────

HOOKS_DIR = "/home/agent/hooks"


def _worker_hooks_settings() -> dict:
    """Return project-level settings with a Stop hook that triggers a reviewer."""
    return {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{HOOKS_DIR}/on-worker-stop.sh",
                        }
                    ]
                }
            ]
        }
    }


def _reviewer_hooks_settings() -> dict:
    """Return project-level settings with a Stop hook that triggers a committer."""
    return {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{HOOKS_DIR}/on-reviewer-stop.sh",
                        }
                    ]
                }
            ]
        }
    }


def write_project_settings(cwd: str, role: str) -> None:
    """Create .claude/settings.json in the working directory with role-appropriate hooks."""
    if role == "committer":
        return  # committer has no hooks

    settings = _worker_hooks_settings() if role == "worker" else _reviewer_hooks_settings()

    settings_dir = os.path.join(cwd, ".claude")
    os.makedirs(settings_dir, exist_ok=True)
    settings_path = os.path.join(settings_dir, "settings.json")
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)


# ── System-prompt builders per role ───────────────────────────────────


def _worker_system_prompt(agent_id: str, branch: str) -> str:
    return (
        f"You are autonomous agent #{agent_id} in a group of agents. "
        "Complete the given task independently and thoroughly. "
        "Provide detailed results when finished."
        "\n\n--- GIT WORKFLOW INSTRUCTIONS ---\n"
        f"You are working inside a git worktree on branch '{branch}'.\n"
        "Your working directory is already set to this worktree.\n"
        "IMPORTANT: As your VERY LAST step, after all other work is complete, you MUST:\n"
        "  1. Run `git add -A` to stage every file you created or changed.\n"
        '  2. Run `git commit -m "Agent work: <short summary of what you did>"` '
        "to commit all changes.\n"
        "Do NOT push. Do NOT switch branches. Just add and commit.\n"
        "--- END GIT WORKFLOW INSTRUCTIONS ---\n"
    )


def _reviewer_system_prompt(agent_id: str, branch: str) -> str:
    return (
        f"You are the REVIEWER agent for branch '{branch}'.\n"
        "Your job is to review all changes committed by the worker agent.\n\n"
        "Follow these steps:\n"
        "  1. Run `git log --oneline main..` to see the worker's commits.\n"
        "  2. Run `git diff main` to see all changes since main.\n"
        "  3. Review the code thoroughly: look for bugs, logic errors,\n"
        "     missing edge cases, security issues, and style problems.\n"
        "  4. If you find issues, FIX them directly in the code.\n"
        "  5. ALWAYS create a file called `review-details.md` in the\n"
        "     working directory. This file MUST contain:\n"
        "       - A summary of what was reviewed\n"
        "       - Any issues found (or 'No issues found')\n"
        "       - Any fixes applied\n"
        "  6. As your VERY LAST step:\n"
        "       a. Run `git add -A`\n"
        '       b. Run `git commit -m "Review: <short summary>"` \n'
        "Do NOT push. Do NOT switch branches.\n"
    )


def _committer_system_prompt(branch: str, repo_dir: str) -> str:
    return (
        f"You are the COMMITTER agent. Your job is to merge branch '{branch}' "
        "into the main branch.\n\n"
        "Follow these steps:\n"
        f"  1. Change directory: `cd {repo_dir}`\n"
        f"  2. Run `git merge {branch}` to merge the branch into main.\n"
        "  3. If there are merge conflicts, resolve them and then run\n"
        "     `git add -A` followed by `git commit -m \"Merge: resolved conflicts\"`.\n"
        "  4. Verify the merge succeeded: `git log --oneline -5`\n"
        "Do NOT push. Do NOT delete the branch.\n"
    )


# ── Main ──────────────────────────────────────────────────────────────


async def main():
    prompt = os.environ.get("AGENT_PROMPT", "")
    agent_id = os.environ.get("AGENT_ID", "0")
    group_id = os.environ.get("JOB_GROUP_ID", "unknown")
    branch = os.environ.get("AGENT_BRANCH", "")
    worktree_path = os.environ.get("AGENT_WORKTREE_PATH", "")
    role = os.environ.get("AGENT_ROLE", "worker")
    repo_dir = os.environ.get("REPO_DIR", "")

    # Determine working directory
    if role == "committer" and repo_dir:
        cwd = repo_dir
    elif worktree_path:
        cwd = worktree_path
    else:
        cwd = "/home/agent/workspace"

    print(f"[{role.upper()} {agent_id}] Starting (group={group_id})")
    print(f"[{role.upper()} {agent_id}] Branch: {branch}")
    print(f"[{role.upper()} {agent_id}] Worktree: {cwd}")
    print(f"[{role.upper()} {agent_id}] Role: {role}")

    # ── Build role-specific system prompt ──
    if role == "reviewer":
        agent_instructions = _reviewer_system_prompt(agent_id, branch)
        effective_prompt = (
            f"Review all changes on branch '{branch}'. "
            "Create review-details.md and commit your review."
        )
    elif role == "committer":
        agent_instructions = _committer_system_prompt(branch, repo_dir)
        effective_prompt = (
            f"Merge branch '{branch}' into main in the repo at {repo_dir}."
        )
    else:
        # worker
        if not prompt:
            print("ERROR: No prompt provided via AGENT_PROMPT env var", file=sys.stderr)
            sys.exit(1)
        agent_instructions = _worker_system_prompt(agent_id, branch)
        effective_prompt = prompt

    print(
        f"[{role.upper()} {agent_id}] Prompt: "
        f"{effective_prompt[:200]}{'...' if len(effective_prompt) > 200 else ''}"
    )

    # ── Write .claude/settings.json with hooks for this role ──
    write_project_settings(cwd, role)

    # ── Configure the Claude Agent SDK ──
    options = ClaudeAgentOptions(
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": agent_instructions,
        },
        mcp_servers={
            "claude-code": {
                "command": "claude",
                "args": ["mcp", "serve"],
            }
        },
        allowed_tools=["mcp__claude-code__*"],
        permission_mode="bypassPermissions",
        max_turns=50,
        cwd=cwd,
    )

    try:
        async for message in query(prompt=effective_prompt, options=options):
            if isinstance(message, ResultMessage):
                result_text = message.result or ""
                print(f"\n{'='*60}")
                print(f"{role.upper()} {agent_id} RESULT")
                print(f"{'='*60}")
                print(result_text)
                print(f"{'='*60}")
                if message.total_cost_usd is not None:
                    print(f"Cost: ${message.total_cost_usd:.4f}")
                if message.duration_ms is not None:
                    print(f"Duration: {message.duration_ms}ms")
                if message.is_error:
                    print(f"Error: {message.is_error}")
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(f"[{role.upper()} {agent_id}] {block.text[:300]}")
                    elif isinstance(block, ToolUseBlock):
                        print(f"[{role.upper()} {agent_id}] Using tool: {block.name}")
    except Exception as e:
        print(f"[{role.upper()} {agent_id}] ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[{role.upper()} {agent_id}] Completed successfully")


if __name__ == "__main__":
    asyncio.run(main())
