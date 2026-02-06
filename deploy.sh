#!/usr/bin/env bash
# deploy.sh - Deploy Multi-Agent Claude MCP to minikube
# Works on both WSL2/Ubuntu 24.04 and macOS
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- Helpers ---
info()  { echo "[INFO]  $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# --- Pre-flight checks ---
check_prerequisites() {
    info "Checking prerequisites..."

    command -v minikube >/dev/null 2>&1 || error "minikube is not installed. Install it from https://minikube.sigs.k8s.io/docs/start/"
    command -v kubectl  >/dev/null 2>&1 || error "kubectl is not installed. Install it from https://kubernetes.io/docs/tasks/tools/"
    command -v docker   >/dev/null 2>&1 || error "docker is not installed. Install Docker Desktop or docker engine."

    info "All prerequisites met."
}

# --- Start minikube if not running ---
ensure_minikube() {
    if minikube status --format='{{.Host}}' 2>/dev/null | grep -q "Running"; then
        info "minikube is already running."
    else
        info "Starting minikube..."
        local os_type
        os_type="$(uname -s)"
        case "$os_type" in
            Linux)
                # WSL2/Ubuntu - use docker driver
                minikube start --driver=docker --memory=4096 --cpus=2
                ;;
            Darwin)
                # macOS - use docker driver (works with Docker Desktop)
                minikube start --driver=docker --memory=4096 --cpus=2
                ;;
            *)
                error "Unsupported OS: $os_type"
                ;;
        esac
        info "minikube started."
    fi
}

# --- Build images inside minikube's Docker ---
build_images() {
    info "Configuring Docker to use minikube's daemon..."
    eval "$(minikube docker-env)"

    info "Building frontend image..."
    docker build -t frontend:latest "$SCRIPT_DIR/frontend"

    info "Building orchestrator image..."
    docker build -t orchestrator:latest "$SCRIPT_DIR/backend/orchestrator"

    info "Building mcp-worker image..."
    docker build -t mcp-worker:latest "$SCRIPT_DIR/backend/mcp-worker"

    info "All images built."
}

# --- Create Kubernetes secret for the API key ---
create_secret() {
    if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
        echo ""
        echo "============================================================"
        echo "  ANTHROPIC_API_KEY is not set."
        echo "  Set it before deploying:"
        echo ""
        echo "    export ANTHROPIC_API_KEY=sk-ant-..."
        echo "    ./deploy.sh"
        echo ""
        echo "  Or create the secret manually after deployment:"
        echo ""
        echo "    kubectl create secret generic anthropic-api-key \\"
        echo "      --namespace=backend \\"
        echo "      --from-literal=api-key=YOUR_KEY"
        echo "============================================================"
        echo ""
        info "Skipping secret creation (set ANTHROPIC_API_KEY and re-run)."
        return
    fi

    info "Creating Anthropic API key secret..."
    kubectl create namespace backend --dry-run=client -o yaml | kubectl apply -f -
    kubectl create secret generic anthropic-api-key \
        --namespace=backend \
        --from-literal=api-key="$ANTHROPIC_API_KEY" \
        --dry-run=client -o yaml | kubectl apply -f -
    info "Secret created."
}

# --- Apply Kubernetes manifests ---
apply_manifests() {
    info "Applying Kubernetes manifests..."

    kubectl apply -f "$SCRIPT_DIR/k8s/namespaces.yaml"
    kubectl apply -f "$SCRIPT_DIR/k8s/backend/orchestrator-rbac.yaml"
    kubectl apply -f "$SCRIPT_DIR/k8s/backend/orchestrator-deployment.yaml"
    kubectl apply -f "$SCRIPT_DIR/k8s/backend/orchestrator-service.yaml"
    kubectl apply -f "$SCRIPT_DIR/k8s/frontend/deployment.yaml"
    kubectl apply -f "$SCRIPT_DIR/k8s/frontend/service.yaml"

    info "Manifests applied."
}

# --- Wait for deployments ---
wait_for_ready() {
    info "Waiting for deployments to be ready..."
    kubectl rollout status deployment/orchestrator -n backend --timeout=120s || true
    kubectl rollout status deployment/frontend -n frontend --timeout=120s || true
    info "Deployments ready."
}

# --- Print access information ---
print_access_info() {
    echo ""
    echo "============================================================"
    echo "  Deployment complete!"
    echo "============================================================"
    echo ""

    local url
    url="$(minikube service frontend -n frontend --url 2>/dev/null || echo "")"
    if [ -n "$url" ]; then
        echo "  Frontend URL: $url"
    else
        echo "  Access via: minikube service frontend -n frontend"
    fi

    echo ""
    echo "  Useful commands:"
    echo "    kubectl get pods -n frontend"
    echo "    kubectl get pods -n backend"
    echo "    kubectl logs -n backend -l app=orchestrator"
    echo "    kubectl get jobs -n backend"
    echo ""
    echo "============================================================"
}

# --- Main ---
main() {
    info "Deploying Multi-Agent Claude MCP to minikube..."
    check_prerequisites
    ensure_minikube
    build_images
    create_secret
    apply_manifests
    wait_for_ready
    print_access_info
}

main "$@"
