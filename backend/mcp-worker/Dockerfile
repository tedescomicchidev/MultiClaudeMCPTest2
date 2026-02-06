# MCP Worker Dockerfile
# Based on the Claude Agent SDK container-based sandboxing guidelines:
# - Node.js runtime (required by Claude Code CLI)
# - Python 3.10+ (required by Claude Agent SDK)
# - Claude Code CLI installed globally
# - Non-root user for security
# - Minimal attack surface

FROM node:20-slim

# Install Python and essential tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    git \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user
RUN useradd -m -s /bin/bash agent && \
    mkdir -p /home/agent/workspace && \
    mkdir -p /home/agent/.claude && \
    mkdir -p /app && \
    chown -R agent:agent /home/agent && \
    chown -R agent:agent /app

USER agent
WORKDIR /home/agent/workspace

# Create Python virtual environment and install dependencies
RUN python3 -m venv /home/agent/venv
ENV PATH="/home/agent/venv/bin:$PATH"

COPY --chown=agent:agent requirements.txt /home/agent/
RUN pip install --no-cache-dir -r /home/agent/requirements.txt

# Copy the worker script to /app for consistent path references in hooks
COPY --chown=agent:agent worker.py /app/worker.py
COPY --chown=agent:agent worker.py /home/agent/

# Copy Claude Code hooks configuration
COPY --chown=agent:agent .claude/ /home/agent/.claude/

CMD ["python3", "/home/agent/worker.py"]
