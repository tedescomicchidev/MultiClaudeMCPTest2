# Multi-Agent Claude MCP

A Kubernetes-based multi-agent system that orchestrates multiple Claude Code MCP instances. Users submit prompts via a web frontend, select the number of agents, and each agent processes the prompt independently in its own git worktree. After each agent finishes, an automated **review-then-merge** pipeline runs via Claude Code hooks.

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
│ │                  │     │ │  WORKER    │ │  WORKER    │   │   │
│ │                  │     │ │  agent-0   │ │  agent-1   │   │   │
│ │                  │     │ │  -> commit │ │  -> commit │   │   │
│ │                  │     │ └─────┬──────┘ └─────┬──────┘   │   │
│ │                  │     │       │ Stop hook     │          │   │
│ │                  │     │       ▼               ▼          │   │
│ │                  │     │ ┌────────────┐ ┌────────────┐   │   │
│ │                  │     │ │  REVIEWER  │ │  REVIEWER  │   │   │
│ │                  │     │ │  agent-0   │ │  agent-1   │   │   │
│ │                  │     │ │  -> review │ │  -> review │   │   │
│ │                  │     │ │  -> commit │ │  -> commit │   │   │
│ │                  │     │ └─────┬──────┘ └─────┬──────┘   │   │
│ │                  │     │       │ Stop hook     │          │   │
│ │                  │     │       ▼               ▼          │   │
│ │                  │     │ ┌────────────┐ ┌────────────┐   │   │
│ │                  │     │ │  COMMITTER │ │  COMMITTER │   │   │
│ │                  │     │ │  agent-0   │ │  agent-1   │   │   │
│ │                  │     │ │  -> merge  │ │  -> merge  │   │   │
│ │                  │     │ └────────────┘ └────────────┘   │   │
│ └──────────────────┘     └──────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### Components

- **Frontend** (namespace: `frontend`): Flask web app where users enter prompts and select agent count
- **Orchestrator** (namespace: `backend`): FastAPI app with 2 load-balanced replicas. On each run it creates a git repo, worktrees, and launches worker Jobs. Also exposes `/api/start-reviewer` and `/api/start-committer` endpoints called by hooks.
- **MCP Worker** (namespace: `backend`): Reusable container image with three roles:

| Role | Trigger | What it does | Hook |
|------|---------|-------------|------|
| **worker** | Orchestrator `/api/run` | Executes the user's prompt, commits to `agent-N` branch | `Stop` hook calls `/api/start-reviewer` |
| **reviewer** | Worker's Stop hook | Reviews commits, creates `review-details.md`, commits fixes | `Stop` hook calls `/api/start-committer` (if `review-details.md` was committed) |
| **committer** | Reviewer's Stop hook | Merges the `agent-N` branch back to `main` | None |

### Hook Pipeline

The pipeline is driven by Claude Code **Stop hooks** configured via `.claude/settings.json` in each agent's working directory:

1. **Worker finishes** → the `on-worker-stop.sh` hook checks if commits exist on the branch → calls `POST /api/start-reviewer`
2. **Reviewer finishes** → the `on-reviewer-stop.sh` hook checks if `review-details.md` was committed → calls `POST /api/start-committer`
3. **Committer finishes** → no hook, pipeline complete

Both hooks guard against infinite re-triggering by checking the `stop_hook_active` field from the Stop event input.

### Storage

| Location | Description |
|----------|-------------|
| `~/code/claude-storage` (host) | Local directory on your machine |
| `/mnt/claude-output` (cluster) | Mounted into minikube via `minikube mount` |

Each run creates:
```
~/code/claude-storage/
  run-<id>/
    repo/           <- main git repository (main branch, receives merges)
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
│       ├── worker.py           # Claude Agent SDK + MCP worker (3 roles)
│       ├── Dockerfile
│       ├── requirements.txt
│       └── hooks/
│           ├── on-worker-stop.sh    # Stop hook: worker -> reviewer
│           └── on-reviewer-stop.sh  # Stop hook: reviewer -> committer
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
3. The orchestrator creates `/mnt/claude-output/run-<id>/repo/`, runs `git init`, and creates one worktree per agent
4. The orchestrator creates one Kubernetes **worker** Job per agent
5. Each worker:
   - Writes a `.claude/settings.json` with a `Stop` hook into its worktree
   - Uses the Claude Agent SDK (`query()`) with `claude mcp serve` as a STDIO MCP server
   - Executes the user's prompt, then `git add -A && git commit`
   - On stop, the hook detects the commit and calls `/api/start-reviewer`
6. Each **reviewer**:
   - Writes a `.claude/settings.json` with a different `Stop` hook
   - Reviews all commits on the branch (`git diff main`)
   - Creates `review-details.md` summarising findings and fixes
   - Commits everything; on stop, the hook detects `review-details.md` and calls `/api/start-committer`
7. Each **committer**:
   - Works in the repo directory (on `main`)
   - Runs `git merge agent-N` to merge the reviewed branch
   - Resolves any conflicts, verifies with `git log`
8. Final result: all agent work is reviewed and merged into `main` at `~/code/claude-storage/run-<id>/repo/`

## Inspecting Results

```bash
# List all runs
ls ~/code/claude-storage/

# See branches for a run
cd ~/code/claude-storage/run-<id>/repo
git branch

# View merged result on main
git log main --oneline

# View agent-0's review
cat ~/code/claude-storage/run-<id>/agent-0/review-details.md

# View agent-0's full diff before merge
git diff main..agent-0
```

## Monitoring

```bash
# Check all pods (workers, reviewers, committers)
kubectl get pods -n backend

# Check all jobs with roles
kubectl get jobs -n backend -L role

# Check a worker's output
kubectl logs -n backend job/mcp-worker-<group-id>-<agent-id>

# Check a reviewer's output
kubectl logs -n backend job/reviewer-<group-id>-<agent-id>

# Check a committer's output
kubectl logs -n backend job/committer-<group-id>-<branch>

# Check orchestrator logs (shows hook-triggered job creation)
kubectl logs -n backend -l app=orchestrator
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
- Stop hooks guard against infinite loops via the `stop_hook_active` field
