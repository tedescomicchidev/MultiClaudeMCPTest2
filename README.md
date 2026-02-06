# Multi-Agent Claude MCP

A Kubernetes-based multi-agent system that orchestrates multiple Claude Code MCP instances. Users submit prompts via a web frontend, select the number of agents, and each agent processes the prompt independently in its own git worktree. Results are committed to per-agent branches and synced back to the host.

## Architecture

```
  ~/code/claude-storage (host)
          │  minikube mount
          ▼
  /mnt/claude-output (cluster)
          │
┌─────────┴────────────────────────────────────────────────────────┐
│                                                                  │
│ ┌──────────────────┐     ┌──────────────────────────────────┐   │
│ │ frontend ns      │     │ backend ns                       │   │
│ │                  │     │                                  │   │
│ │ ┌──────────────┐ │     │ ┌──────────────────┐            │   │
│ │ │  Flask Web   │─┼─────┼▶│  Orchestrator    │            │   │
│ │ │  App (UI)    │ │     │ │  (FastAPI x2)    │            │   │
│ │ └──────────────┘ │     │ └──────┬───────────┘            │   │
│ │                  │     │        │                         │   │
│ │                  │     │        │ 1. git init repo        │   │
│ │                  │     │        │ 2. git worktree add     │   │
│ │                  │     │        │ 3. create K8s Jobs      │   │
│ │                  │     │        ▼                         │   │
│ │                  │     │ ┌────────────┐ ┌────────────┐   │   │
│ │                  │     │ │ MCP Worker │ │ MCP Worker │   │   │
│ │                  │     │ │ (Job)      │ │ (Job)      │   │   │
│ │                  │     │ │            │ │            │   │   │
│ │                  │     │ │ branch:    │ │ branch:    │   │   │
│ │                  │     │ │  agent-0   │ │  agent-1   │   │   │
│ │                  │     │ │ cwd:       │ │ cwd:       │   │   │
│ │                  │     │ │  worktree  │ │  worktree  │   │   │
│ │                  │     │ │ -> commit  │ │ -> commit  │   │   │
│ │                  │     │ └────────────┘ └────────────┘   │   │
│ └──────────────────┘     └──────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### Components

- **Frontend** (namespace: `frontend`): Flask web app where users enter prompts and select agent count
- **Orchestrator** (namespace: `backend`): FastAPI app with 2 load-balanced replicas. On each run it:
  1. Creates a run directory under `/mnt/claude-output`
  2. Initialises a git repo with an initial commit
  3. Creates one git worktree + branch per agent
  4. Launches one K8s Job per agent, passing the worktree path and branch name
- **MCP Worker** (namespace: `backend`): One K8s Job per agent. Each pod runs `claude mcp serve` (STDIO MCP) via the Claude Agent SDK, uses its assigned worktree as `cwd`, and commits all changes to its branch as the final step.

### Storage

| Location | Description |
|----------|-------------|
| `~/code/claude-storage` (host) | Local directory on your machine |
| `/mnt/claude-output` (cluster) | Mounted into minikube via `minikube mount` |

Each run creates:
```
~/code/claude-storage/
  run-<id>/
    repo/           <- main git repository
    agent-0/        <- worktree on branch agent-0
    agent-1/        <- worktree on branch agent-1
    ...
```

## Prerequisites

- [minikube](https://minikube.sigs.k8s.io/docs/start/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [Docker](https://docs.docker.com/get-docker/)
- An [Anthropic API key](https://console.anthropic.com/)

## Quick Start

```bash
# 1. Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# 2. Deploy everything (creates ~/code/claude-storage, starts minikube with mount)
./deploy.sh

# 3. Access the UI
minikube service frontend -n frontend
```

## Manual Deployment

```bash
# Create the host storage directory
mkdir -p ~/code/claude-storage

# Start minikube with the host mount
minikube start --driver=docker --memory=4096 --cpus=2 \
  --mount --mount-string="$HOME/code/claude-storage:/mnt/claude-output"

# Build images inside minikube's Docker
eval $(minikube docker-env)
docker build -t frontend:latest frontend/
docker build -t orchestrator:latest backend/orchestrator/
docker build -t mcp-worker:latest backend/mcp-worker/

# Create namespaces
kubectl apply -f k8s/namespaces.yaml

# Create the API key secret
kubectl create secret generic anthropic-api-key \
  --namespace=backend \
  --from-literal=api-key=$ANTHROPIC_API_KEY

# Deploy storage, backend, and frontend
kubectl apply -f k8s/backend/
kubectl apply -f k8s/frontend/

# Access the UI
minikube service frontend -n frontend
```

## Project Structure

```
.
├── frontend/
│   ├── app.py                  # Flask web application
│   ├── Dockerfile
│   ├── requirements.txt
│   └── templates/
│       └── index.html          # Web UI
├── backend/
│   ├── orchestrator/
│   │   ├── app.py              # FastAPI orchestrator (git + K8s Jobs)
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── mcp-worker/
│       ├── worker.py           # Claude Agent SDK + MCP worker
│       ├── Dockerfile
│       └── requirements.txt
├── k8s/
│   ├── namespaces.yaml
│   ├── secret.yaml             # API key secret template
│   ├── frontend/
│   │   ├── deployment.yaml
│   │   └── service.yaml
│   └── backend/
│       ├── orchestrator-deployment.yaml
│       ├── orchestrator-service.yaml
│       ├── orchestrator-rbac.yaml
│       └── storage.yaml        # PV + PVC for shared output
├── deploy.sh                   # One-command deployment script
└── README.md
```

## How It Works

1. User enters a prompt and selects the number of agents in the web UI
2. The frontend sends a POST request to the orchestrator
3. The orchestrator:
   a. Creates `/mnt/claude-output/run-<id>/repo/` and runs `git init`
   b. Makes an initial commit
   c. For each agent, runs `git worktree add -b agent-N` to create a worktree and branch
4. The orchestrator creates one Kubernetes Job per agent, passing:
   - `AGENT_WORKTREE_PATH` -- the worktree directory as the agent's working directory
   - `AGENT_BRANCH` -- the branch name for the agent
5. Each Job pod runs the MCP worker which:
   - Uses the Claude Agent SDK (`query()`) with `claude mcp serve` as a STDIO MCP server
   - Receives system-prompt instructions to `git add -A && git commit` as the final step
   - Works in its own worktree so agents never conflict
6. After all agents finish, each agent's work is on a separate branch visible at `~/code/claude-storage/run-<id>/`

## Inspecting Results

```bash
# List all runs
ls ~/code/claude-storage/

# See branches for a run
cd ~/code/claude-storage/run-<id>/repo
git branch

# View agent-0's diff
git diff main..agent-0

# View agent-1's log
git log agent-1 --oneline
```

## Monitoring

```bash
# Check pods in all namespaces
kubectl get pods -n frontend
kubectl get pods -n backend

# Check orchestrator logs
kubectl logs -n backend -l app=orchestrator

# Check MCP worker jobs
kubectl get jobs -n backend

# Check a specific worker's output
kubectl logs -n backend job/mcp-worker-<group-id>-<agent-id>
```

## Platform Support

| Platform | Driver | Status |
|----------|--------|--------|
| WSL2 / Ubuntu 24.04 | docker | Supported |
| macOS (Intel/Apple Silicon) | docker | Supported |

## Security

- All containers run as non-root users (UID 1000)
- `allowPrivilegeEscalation` is disabled on all containers
- The orchestrator uses a dedicated ServiceAccount with minimal RBAC permissions (Jobs, Pods, Pod logs only)
- MCP workers use `bypassPermissions` within the container sandbox -- the container itself is the security boundary
- The Anthropic API key is stored as a Kubernetes Secret and injected via env vars
- MCP worker pods are ephemeral (Jobs with `ttlSecondsAfterFinished: 3600`)
- Shared storage uses a hostPath PV scoped to `~/code/claude-storage`
