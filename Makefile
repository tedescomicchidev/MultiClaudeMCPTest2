# Makefile - Deploy Multi-Agent Claude MCP to minikube
# Works on both WSL2/Ubuntu 24.04 and macOS

.PHONY: all deploy check-prerequisites ensure-minikube build-images create-secret \
        apply-manifests wait-for-ready print-access-info clean status logs help

# Default target
all: deploy

# Full deployment pipeline
deploy: check-prerequisites ensure-minikube build-images create-secret apply-manifests wait-for-ready print-access-info

# --- Pre-flight checks ---
check-prerequisites:
	@echo "[INFO]  Checking prerequisites..."
	@command -v minikube >/dev/null 2>&1 || { echo "[ERROR] minikube is not installed. Install it from https://minikube.sigs.k8s.io/docs/start/"; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo "[ERROR] kubectl is not installed. Install it from https://kubernetes.io/docs/tasks/tools/"; exit 1; }
	@command -v docker >/dev/null 2>&1 || { echo "[ERROR] docker is not installed. Install Docker Desktop or docker engine."; exit 1; }
	@echo "[INFO]  All prerequisites met."

# --- Start minikube if not running ---
ensure-minikube:
	@echo "[INFO]  Checking minikube status..."
	@if minikube status --format='{{.Host}}' 2>/dev/null | grep -q "Running"; then \
		echo "[INFO]  minikube is already running."; \
	else \
		echo "[INFO]  Starting minikube..."; \
		minikube start --driver=docker --memory=4096 --cpus=2; \
		echo "[INFO]  minikube started."; \
	fi

# --- Build images inside minikube's Docker ---
build-images:
	@echo "[INFO]  Configuring Docker to use minikube's daemon..."
	@eval $$(minikube docker-env) && \
		echo "[INFO]  Building frontend image..." && \
		docker build -t frontend:latest ./frontend && \
		echo "[INFO]  Building orchestrator image..." && \
		docker build -t orchestrator:latest ./backend/orchestrator && \
		echo "[INFO]  Building mcp-worker image..." && \
		docker build -t mcp-worker:latest ./backend/mcp-worker && \
		echo "[INFO]  All images built."

# Individual image build targets
build-frontend:
	@eval $$(minikube docker-env) && docker build -t frontend:latest ./frontend

build-orchestrator:
	@eval $$(minikube docker-env) && docker build -t orchestrator:latest ./backend/orchestrator

build-mcp-worker:
	@eval $$(minikube docker-env) && docker build -t mcp-worker:latest ./backend/mcp-worker

# --- Create Kubernetes secret for the API key ---
create-secret:
	@if [ -z "$${ANTHROPIC_API_KEY:-}" ]; then \
		echo ""; \
		echo "============================================================"; \
		echo "  ANTHROPIC_API_KEY is not set."; \
		echo "  Set it before deploying:"; \
		echo ""; \
		echo "    export ANTHROPIC_API_KEY=sk-ant-..."; \
		echo "    make deploy"; \
		echo ""; \
		echo "  Or create the secret manually after deployment:"; \
		echo ""; \
		echo "    kubectl create secret generic anthropic-api-key \\"; \
		echo "      --namespace=backend \\"; \
		echo "      --from-literal=api-key=YOUR_KEY"; \
		echo "============================================================"; \
		echo ""; \
		echo "[INFO]  Skipping secret creation (set ANTHROPIC_API_KEY and re-run)."; \
	else \
		echo "[INFO]  Creating Anthropic API key secret..."; \
		kubectl create namespace backend --dry-run=client -o yaml | kubectl apply -f -; \
		kubectl create secret generic anthropic-api-key \
			--namespace=backend \
			--from-literal=api-key="$$ANTHROPIC_API_KEY" \
			--dry-run=client -o yaml | kubectl apply -f -; \
		echo "[INFO]  Secret created."; \
	fi

# --- Apply Kubernetes manifests ---
apply-manifests:
	@echo "[INFO]  Applying Kubernetes manifests..."
	@kubectl apply -f ./k8s/namespaces.yaml
	@kubectl apply -f ./k8s/backend/orchestrator-rbac.yaml
	@kubectl apply -f ./k8s/backend/orchestrator-deployment.yaml
	@kubectl apply -f ./k8s/backend/orchestrator-service.yaml
	@kubectl apply -f ./k8s/frontend/deployment.yaml
	@kubectl apply -f ./k8s/frontend/service.yaml
	@echo "[INFO]  Manifests applied."

# --- Wait for deployments ---
wait-for-ready:
	@echo "[INFO]  Waiting for deployments to be ready..."
	@kubectl rollout status deployment/orchestrator -n backend --timeout=120s || true
	@kubectl rollout status deployment/frontend -n frontend --timeout=120s || true
	@echo "[INFO]  Deployments ready."

# --- Print access information ---
print-access-info:
	@echo ""
	@echo "============================================================"
	@echo "  Deployment complete!"
	@echo "============================================================"
	@echo ""
	@url=$$(minikube service frontend -n frontend --url 2>/dev/null || echo ""); \
	if [ -n "$$url" ]; then \
		echo "  Frontend URL: $$url"; \
	else \
		echo "  Access via: minikube service frontend -n frontend"; \
	fi
	@echo ""
	@echo "  Useful commands:"
	@echo "    kubectl get pods -n frontend"
	@echo "    kubectl get pods -n backend"
	@echo "    kubectl logs -n backend -l app=orchestrator"
	@echo "    kubectl get jobs -n backend"
	@echo ""
	@echo "============================================================"

# --- Utility targets ---

# Show cluster status
status:
	@echo "[INFO]  Cluster Status:"
	@echo "--- Minikube ---"
	@minikube status || true
	@echo ""
	@echo "--- Frontend Pods ---"
	@kubectl get pods -n frontend 2>/dev/null || echo "  Namespace not found"
	@echo ""
	@echo "--- Backend Pods ---"
	@kubectl get pods -n backend 2>/dev/null || echo "  Namespace not found"
	@echo ""
	@echo "--- Jobs ---"
	@kubectl get jobs -n backend 2>/dev/null || echo "  No jobs found"

# View orchestrator logs
logs:
	@kubectl logs -n backend -l app=orchestrator --tail=100 -f

logs-frontend:
	@kubectl logs -n frontend -l app=frontend --tail=100 -f

# Open frontend in browser
open:
	@minikube service frontend -n frontend

# Clean up resources
clean:
	@echo "[INFO]  Cleaning up Kubernetes resources..."
	@kubectl delete pods -l app=mcp-worker -n backend
	@kubectl delete -f ./k8s/frontend/ --ignore-not-found=true || true
	@kubectl delete -f ./k8s/backend/ --ignore-not-found=true || true
	@kubectl delete -f ./k8s/namespaces.yaml --ignore-not-found=true || true
	@docker rmi orchestrator:latest
	@docker rmi frontend:latest
	@docker rmi mcp-worker:latest
	@echo "[INFO]  Cleanup complete."

# Stop minikube
stop:
	@echo "[INFO]  Stopping minikube..."
	@minikube stop
	@echo "[INFO]  minikube stopped."

# Delete minikube cluster
delete-cluster:
	@echo "[INFO]  Deleting minikube cluster..."
	@minikube delete
	@echo "[INFO]  minikube cluster deleted."

# Restart deployments
restart:
	@echo "[INFO]  Restarting deployments..."
	@kubectl rollout restart deployment/orchestrator -n backend
	@kubectl rollout restart deployment/frontend -n frontend
	@echo "[INFO]  Restart initiated."

# Help
help:
	@echo "Multi-Agent Claude MCP Deployment Makefile"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Main Targets:"
	@echo "  deploy              Full deployment pipeline (default)"
	@echo "  check-prerequisites Check for required tools"
	@echo "  ensure-minikube     Start minikube if not running"
	@echo "  build-images        Build all Docker images"
	@echo "  create-secret       Create Anthropic API key secret"
	@echo "  apply-manifests     Apply Kubernetes manifests"
	@echo "  wait-for-ready      Wait for deployments to be ready"
	@echo ""
	@echo "Individual Build Targets:"
	@echo "  build-frontend      Build frontend image only"
	@echo "  build-orchestrator  Build orchestrator image only"
	@echo "  build-mcp-worker    Build mcp-worker image only"
	@echo ""
	@echo "Utility Targets:"
	@echo "  status              Show cluster and pod status"
	@echo "  logs                Follow orchestrator logs"
	@echo "  logs-frontend       Follow frontend logs"
	@echo "  open                Open frontend in browser"
	@echo "  restart             Restart all deployments"
	@echo "  clean               Delete Kubernetes resources"
	@echo "  stop                Stop minikube"
	@echo "  delete-cluster      Delete minikube cluster"
	@echo "  help                Show this help message"
	@echo ""
	@echo "Environment Variables:"
	@echo "  ANTHROPIC_API_KEY   Required for create-secret target"
