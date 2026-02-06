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
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user
RUN useradd -m -s /bin/bash agent && \
    mkdir -p /home/agent/workspace && \
    mkdir -p /home/agent/.claude && \
    chown -R agent:agent /home/agent

USER agent
WORKDIR /home/agent/workspace

# Create Python virtual environment and install dependencies
RUN python3 -m venv /home/agent/venv
ENV PATH="/home/agent/venv/bin:$PATH"

COPY --chown=agent:agent requirements.txt /home/agent/
RUN pip install --no-cache-dir -r /home/agent/requirements.txt

COPY --chown=agent:agent worker.py /home/agent/

CMD ["python3", "/home/agent/worker.py"]
