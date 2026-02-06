I want to build following solution to run on my minikube on my windows wsl2 with ubuntu 24.04 and also on macos.

# Docs
Read carefully all the attached documentations to be able to leverage the Claude SDK for python and all the related information in the best way.

# frontend
A simple python web app, which runs in the frontend namespace. The end user can browse the app and enter a prompt and select an amount of agents that need to work on this prompt.
A go button will call and instruct the backend.

# backend
- a orchestrator app written in python which is called when the go button on the frontend is clicked. The orchestrator app needs to run on the namespace backend.
The Orchestrator app needs to run in a container, loadbalanced so that 2 instances are always up and running.
- an MCP running in an other container. The MCP is called via docker and it is an STDIO MCP for "claude mcp serve". It hosts the Claude Code CLI in form of an MCP server. 
The mcp instances will also run in the backend namespace.
The orchestrator app calls the MCP as many times as the user has defined via the UI. This will create one pod per MCP instance. 
Each instance of the MCP will take the prompt as an input and as soon as the work is finished, the container can be terminated.

# additional requirements (stored in the git repository)
- Read carefully the section "Sandbox Configuration" in "Agent SDK reference - Python - Sandbox Configuration.md" to better understand how to best configure the backend orchestrator to run in a sandboxed container.
- Read carefully the section "Sandbox settings" in "Claude Code settings - Sandbox settings.md" to better understand how to set specific settings for the orchestrator app.
- Read carefully "Configure permissions.md" to understand how to apply bypassPermissions.
- Read carefully "Connect to external tools with MCP.md" to understand how the orchestrator app can call an MCP (stdio).
- Read carefully "Custom Tools.md" to define the wrap the docker-based MCP server in a custom tool.
- Read carefully the section "Container-Based Sandboxing" of "Hosting the Agent SDK - Container-Based Sandboxing.md" to understand how to write a Dockerfile for the orchestrator app.
- Read carefully "Modifying system prompts.md" to add additional prompt instructions for each agent.
- Read carefully "Securely deploying AI agents - Containers.md", especially the "Containers" section, to apply security best practices to run the Agent SDK (orchestrator app) in a container.
- Read "Session Management.md" in case you need to leverage sessions and session details.
