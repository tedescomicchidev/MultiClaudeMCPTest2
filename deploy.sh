#!/usr/bin/env bash
# deploy.sh - Deploy Multi-Agent Claude MCP to minikube
# Works on both WSL2/Ubuntu 24.04 and macOS
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Host directory that will be mounted into the minikube VM and exposed
# as /mnt/claude-output inside every pod via a hostPath PV.
HOST_STORAGE_DIR="${HOME}/code/claude-storage"

# --- Helpers ---
info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# --- Pre-flight checks ---
check_prerequisites() {
    info "Checking prerequisites..."

    command -v minikube >/dev/null 2>&1 || error "minikube is not installed. Install it from https://minikube.sigs.k8s.io/docs/start/"
    command -v kubectl  >/dev/null 2>&1 || error "kubectl is not installed. Install it from https://kubernetes.io/docs/tasks/tools/"
    command -v docker   >/dev/null 2>&1 || error "docker is not installed. Install Docker Desktop or docker engine."

    info "All prerequisites met."
}

# --- Ensure the host storage directory exists ---
ensure_host_storage() {
    if [ ! -d "$HOST_STORAGE_DIR" ]; then
        info "Creating host storage directory: $HOST_STORAGE_DIR"
        mkdir -p "$HOST_STORAGE_DIR"
    else
        info "Host storage directory exists: $HOST_STORAGE_DIR"
    fi
}

# --- Start minikube if not running ---
ensure_minikube() {
    if minikube status --format='{{.Host}}' 2>/dev/null | grep -q "Running"; then
        info "minikube is already running."
    else
        info "Starting minikube with host mount..."
        local os_type
        os_type="$(uname -s)"
        case "$os_type" in
            Linux|Darwin)
                minikube start --driver=docker --memory=4096 --cpus=2 \
                    --mount --mount-string="${HOST_STORAGE_DIR}:/mnt/claude-output"
                ;;
            *)
                error "Unsupported OS: $os_type"
                ;;
        esac
        info "minikube started with mount ${HOST_STORAGE_DIR} -> /mnt/claude-output."
    fi
}

# --- Ensure the mount is active (even if minikube was already running) ---
ensure_mount() {
    # Check whether the mount is already active by looking for a running
    # `minikube mount` process with our mount string.
    if pgrep -f "minikube mount.*${HOST_STORAGE_DIR}:/mnt/claude-output" >/dev/null 2>&1; then
        info "minikube mount already active."
        return
    fi

    # If minikube was started *without* --mount, start a background mount.
    info "Starting background minikube mount: ${HOST_STORAGE_DIR} -> /mnt/claude-output"
    nohup minikube mount "${HOST_STORAGE_DIR}:/mnt/claude-output" \
        > /tmp/minikube-mount.log 2>&1 &
    MOUNT_PID=$!
    sleep 2

    if kill -0 "$MOUNT_PID" 2>/dev/null; then
        info "minikube mount running (PID $MOUNT_PID). Log: /tmp/minikube-mount.log"
    else
        warn "minikube mount may have failed. Check /tmp/minikube-mount.log"
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

    # Storage (PV + PVC) must exist before deployments reference the PVC
    kubectl apply -f "$SCRIPT_DIR/k8s/backend/storage.yaml"

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
    echo "  Host storage: $HOST_STORAGE_DIR"
    echo "  Cluster path: /mnt/claude-output"
    echo ""
    echo "  Each run creates:"
    echo "    $HOST_STORAGE_DIR/run-<id>/repo/     <- main git repo"
    echo "    $HOST_STORAGE_DIR/run-<id>/agent-N/  <- worktree per agent"
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
    ensure_host_storage
    ensure_minikube
    ensure_mount
    build_images
    create_secret
    apply_manifests
    wait_for_ready
    print_access_info
}

main "$@"
