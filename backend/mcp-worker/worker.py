"""MCP Worker: runs a Claude Agent SDK query with 'claude mcp serve' as the STDIO MCP server.

Each worker pod receives a prompt via the AGENT_PROMPT environment variable,
processes it using the Claude Agent SDK backed by the Claude Code MCP server,
and outputs the results to stdout (collected via pod logs by the orchestrator).
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


async def main():
    prompt = os.environ.get("AGENT_PROMPT", "")
    agent_id = os.environ.get("AGENT_ID", "0")
    group_id = os.environ.get("JOB_GROUP_ID", "unknown")

    if not prompt:
        print("ERROR: No prompt provided via AGENT_PROMPT env var", file=sys.stderr)
        sys.exit(1)

    print(f"[Agent {agent_id}] Starting (group={group_id})")
    print(f"[Agent {agent_id}] Prompt: {prompt[:200]}{'...' if len(prompt) > 200 else ''}")

    # Configure the Claude Agent SDK with 'claude mcp serve' as an STDIO MCP server.
    # This exposes Claude Code's built-in tools (Read, Write, Edit, Bash, etc.)
    # through the MCP protocol, making them available to the agent.
    options = ClaudeAgentOptions(
        # Use Claude Code's system prompt with additional instructions for this agent
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": (
                f"You are autonomous agent #{agent_id} in a group of agents. "
                "Complete the given task independently and thoroughly. "
                "Provide detailed results when finished."
            ),
        },
        # Configure 'claude mcp serve' as the STDIO MCP server
        mcp_servers={
            "claude-code": {
                "command": "claude",
                "args": ["mcp", "serve"],
            }
        },
        # Allow all tools from the MCP server
        allowed_tools=["mcp__claude-code__*"],
        # Bypass all permission checks for autonomous operation in container
        permission_mode="bypassPermissions",
        # Limit turns to prevent runaway agents
        max_turns=50,
        # Set the working directory
        cwd="/home/agent/workspace",
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
