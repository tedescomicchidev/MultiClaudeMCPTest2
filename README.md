# Multi-Agent Claude MCP

A Kubernetes-based multi-agent system that orchestrates multiple Claude Code MCP instances. Users submit prompts via a web frontend, select the number of agents, and each agent processes the prompt independently in its own container.

## Architecture

```
┌──────────────────────┐     ┌──────────────────────────────────────────────┐
│  frontend namespace  │     │            backend namespace                 │
│                      │     │                                              │
│  ┌────────────────┐  │     │  ┌──────────────────┐                       │
│  │   Flask Web    │──┼─────┼─▶│  Orchestrator    │                       │
│  │   App (UI)     │  │     │  │  (FastAPI x2)    │                       │
│  └────────────────┘  │     │  └──────┬───────────┘                       │
│                      │     │         │ creates K8s Jobs                   │
│                      │     │         ▼                                    │
│                      │     │  ┌─────────────┐  ┌─────────────┐           │
│                      │     │  │ MCP Worker  │  │ MCP Worker  │  ...      │
│                      │     │  │ Pod (Job)   │  │ Pod (Job)   │           │
│                      │     │  │             │  │             │           │
│                      │     │  │ claude mcp  │  │ claude mcp  │           │
│                      │     │  │   serve     │  │   serve     │           │
│                      │     │  │ (STDIO MCP) │  │ (STDIO MCP) │           │
│                      │     │  └─────────────┘  └─────────────┘           │
└──────────────────────┘     └──────────────────────────────────────────────┘
```

### Components

- **Frontend** (namespace: `frontend`): Flask web app where users enter prompts and select agent count
- **Orchestrator** (namespace: `backend`): FastAPI app with 2 load-balanced replicas that creates and monitors K8s Jobs
- **MCP Worker** (namespace: `backend`): One K8s Job per agent. Each pod runs `claude mcp serve` (STDIO MCP) via the Claude Agent SDK. The container terminates after the prompt is processed.

## Prerequisites

- [minikube](https://minikube.sigs.k8s.io/docs/start/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [Docker](https://docs.docker.com/get-docker/)
- An [Anthropic API key](https://console.anthropic.com/)

## Quick Start

```bash
# 1. Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# 2. Deploy everything
./deploy.sh

# 3. Access the UI
minikube service frontend -n frontend
```

## Manual Deployment

```bash
# Start minikube
minikube start --driver=docker --memory=4096 --cpus=2

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

# Deploy backend (RBAC, orchestrator, service)
kubectl apply -f k8s/backend/

# Deploy frontend
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
│   │   ├── app.py              # FastAPI orchestrator
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
│       └── orchestrator-rbac.yaml
├── deploy.sh                   # One-command deployment script
└── README.md
```

## How It Works

1. User enters a prompt and selects the number of agents in the web UI
2. The frontend sends a POST request to the orchestrator
3. The orchestrator creates one Kubernetes Job per agent in the `backend` namespace
4. Each Job pod runs the MCP worker which:
   - Uses the Claude Agent SDK (`query()`) with `claude mcp serve` as a STDIO MCP server
   - Processes the prompt with `bypassPermissions` for autonomous operation
   - Outputs results to stdout (collected via pod logs)
5. The frontend polls the orchestrator for job status and displays results

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
