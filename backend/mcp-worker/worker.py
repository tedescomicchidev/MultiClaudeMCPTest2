"""MCP Worker: runs a Claude Agent SDK query with 'claude mcp serve' as the STDIO MCP server.

Each worker pod receives:
  - AGENT_PROMPT          : the user's prompt
  - AGENT_ID              : numeric agent identifier
  - JOB_GROUP_ID          : run group identifier
  - AGENT_BRANCH          : git branch assigned to this agent
  - AGENT_WORKTREE_PATH   : path to the git worktree for this agent

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


async def main():
    prompt = os.environ.get("AGENT_PROMPT", "")
    agent_id = os.environ.get("AGENT_ID", "0")
    group_id = os.environ.get("JOB_GROUP_ID", "unknown")
    branch = os.environ.get("AGENT_BRANCH", "")
    worktree_path = os.environ.get("AGENT_WORKTREE_PATH", "")

    if not prompt:
        print("ERROR: No prompt provided via AGENT_PROMPT env var", file=sys.stderr)
        sys.exit(1)

    # Fall back to a generic workspace when no worktree is provided
    cwd = worktree_path if worktree_path else "/home/agent/workspace"

    print(f"[Agent {agent_id}] Starting (group={group_id})")
    print(f"[Agent {agent_id}] Branch: {branch}")
    print(f"[Agent {agent_id}] Worktree: {cwd}")
    print(f"[Agent {agent_id}] Prompt: {prompt[:200]}{'...' if len(prompt) > 200 else ''}")

    # Build the system prompt appendix
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
                print(f"AGENT {agent_id} RESULT")
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
