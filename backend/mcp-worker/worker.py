"""MCP Worker: runs a Claude Agent SDK query with 'claude mcp serve' as the STDIO MCP server.

Each worker pod receives:
  - AGENT_PROMPT          : the user's prompt
  - AGENT_ID              : numeric agent identifier
  - JOB_GROUP_ID          : run group identifier
  - AGENT_BRANCH          : git branch assigned to this agent
  - AGENT_WORKTREE_PATH   : path to the git worktree for this agent
  - AGENT_TYPE            : type of worker (worker, reviewer, committer)

Worker types:
  - worker    : performs the main task and commits changes
  - reviewer  : reviews the commit, fixes issues, creates review-details.md, then commits
  - committer : merges the agent branch back to main

The worker uses the worktree as its working directory and appends
instructions to the system prompt so the agent commits all changes to
its assigned branch as the final step.
"""

import asyncio
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


def build_commit_instructions(branch: str) -> str:
    """Return the system-prompt appendix that tells the agent to commit."""
    return (
        "\n\n--- GIT WORKFLOW INSTRUCTIONS ---\n"
        f"You are working inside a git worktree on branch '{branch}'.\n"
        "Your working directory is already set to this worktree.\n"
        "IMPORTANT: As your VERY LAST step, after all other work is complete, you MUST:\n"
        "  1. Run `git add -A` to stage every file you created or changed.\n"
        f'  2. Run `git commit -m "Agent work: <short summary of what you did>"` '
        "to commit all changes.\n"
        "Do NOT push. Do NOT switch branches. Just add and commit.\n"
        "--- END GIT WORKFLOW INSTRUCTIONS ---\n"
    )


def build_reviewer_instructions(branch: str) -> str:
    """Return the system-prompt appendix for a reviewer agent."""
    return (
        "\n\n--- REVIEWER WORKFLOW INSTRUCTIONS ---\n"
        f"You are a REVIEWER agent working on branch '{branch}'.\n"
        "Your working directory is already set to the git worktree for this branch.\n"
        "\n"
        "YOUR TASK:\n"
        "1. Review the latest commit on this branch by running `git log -1 --stat` and `git show`.\n"
        "2. Examine the changed files for:\n"
        "   - Code correctness and potential bugs\n"
        "   - Security vulnerabilities\n"
        "   - Code style and best practices\n"
        "   - Missing error handling\n"
        "   - Potential improvements\n"
        "3. If you find issues that MUST be fixed:\n"
        "   - Make the necessary corrections directly in the code files\n"
        "4. Create a file named 'review-details.md' in the repository root with:\n"
        "   - Summary of what was reviewed\n"
        "   - List of files examined\n"
        "   - Issues found (if any)\n"
        "   - Fixes applied (if any)\n"
        "   - Overall assessment (approved/needs-work)\n"
        "\n"
        "IMPORTANT: As your VERY LAST step:\n"
        "  1. Run `git add -A` to stage all changes (including review-details.md).\n"
        f'  2. Run `git commit -m "Review: <short summary of review outcome>"` '
        "to commit.\n"
        "Do NOT push. Do NOT switch branches. Just add and commit.\n"
        "--- END REVIEWER WORKFLOW INSTRUCTIONS ---\n"
    )


def build_committer_instructions(branch: str) -> str:
    """Return the system-prompt appendix for a committer agent that merges to main."""
    return (
        "\n\n--- COMMITTER WORKFLOW INSTRUCTIONS ---\n"
        f"You are a COMMITTER agent working on branch '{branch}'.\n"
        "Your working directory is already set to the git worktree for this branch.\n"
        "\n"
        "YOUR TASK:\n"
        "1. First, verify that 'review-details.md' exists and check its content.\n"
        "2. Read the review-details.md to confirm the review was completed.\n"
        "3. If the review indicates approval, proceed with the merge:\n"
        "   a. Run `git checkout main` to switch to the main branch.\n"
        f"   b. Run `git merge {branch} --no-ff -m \"Merge {branch}: <summary>\"` "
        "to merge the agent branch.\n"
        "4. If the review indicates issues that weren't fixed, DO NOT merge.\n"
        "   Instead, report the status and explain why the merge was not performed.\n"
        "\n"
        "IMPORTANT:\n"
        "- Only merge if the review indicates the code is ready.\n"
        "- Do NOT push to remote. Just perform the local merge.\n"
        "- Do NOT delete the branch after merging.\n"
        "--- END COMMITTER WORKFLOW INSTRUCTIONS ---\n"
    )


async def main():
    prompt = os.environ.get("AGENT_PROMPT", "")
    agent_id = os.environ.get("AGENT_ID", "0")
    group_id = os.environ.get("JOB_GROUP_ID", "unknown")
    branch = os.environ.get("AGENT_BRANCH", "")
    worktree_path = os.environ.get("AGENT_WORKTREE_PATH", "")
    agent_type = os.environ.get("AGENT_TYPE", "worker")  # worker, reviewer, committer

    if not prompt and agent_type == "worker":
        print("ERROR: No prompt provided via AGENT_PROMPT env var", file=sys.stderr)
        sys.exit(1)

    # Fall back to a generic workspace when no worktree is provided
    cwd = worktree_path if worktree_path else "/home/agent/workspace"

    print(f"[Agent {agent_id}] Starting (group={group_id}, type={agent_type})")
    print(f"[Agent {agent_id}] Branch: {branch}")
    print(f"[Agent {agent_id}] Worktree: {cwd}")
    if prompt:
        print(f"[Agent {agent_id}] Prompt: {prompt[:200]}{'...' if len(prompt) > 200 else ''}")

    # Build the system prompt appendix based on agent type
    if agent_type == "reviewer":
        agent_instructions = (
            f"You are REVIEWER agent #{agent_id} in a group of agents. "
            "Your job is to review the latest commit on this branch, identify any issues, "
            "fix critical problems, and document your review findings."
        )
        if branch:
            agent_instructions += build_reviewer_instructions(branch)
        # Default prompt for reviewer if none provided
        if not prompt:
            prompt = "Review the latest commit on this branch."
    elif agent_type == "committer":
        agent_instructions = (
            f"You are COMMITTER agent #{agent_id} in a group of agents. "
            "Your job is to verify the review was completed and merge the branch to main."
        )
        if branch:
            agent_instructions += build_committer_instructions(branch)
        # Default prompt for committer if none provided
        if not prompt:
            prompt = "Merge the reviewed branch to main."
    else:  # worker (default)
        agent_instructions = (
            f"You are autonomous agent #{agent_id} in a group of agents. "
            "Complete the given task independently and thoroughly. "
            "Provide detailed results when finished."
        )
        if branch:
            agent_instructions += build_commit_instructions(branch)

    # Configure the Claude Agent SDK with 'claude mcp serve' as an STDIO MCP server.
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

    result_text = ""
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                result_text = message.result or ""
                print(f"\n{'='*60}")
                print(f"AGENT {agent_id} ({agent_type.upper()}) RESULT")
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
                        print(f"[Agent {agent_id}] {block.text[:300]}")
                    elif isinstance(block, ToolUseBlock):
                        print(f"[Agent {agent_id}] Using tool: {block.name}")
    except Exception as e:
        print(f"[Agent {agent_id}] ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[Agent {agent_id}] Completed successfully")


if __name__ == "__main__":
    asyncio.run(main())
