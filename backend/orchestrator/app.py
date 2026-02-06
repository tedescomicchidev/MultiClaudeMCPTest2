import logging
import os
import subprocess
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from kubernetes import client, config
from pydantic import BaseModel

logger = logging.getLogger("orchestrator")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Multi-Agent Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load Kubernetes configuration
try:
    config.load_incluster_config()
except config.ConfigException:
    config.load_kube_config()

batch_v1 = client.BatchV1Api()
core_v1 = client.CoreV1Api()

MCP_WORKER_IMAGE = os.environ.get("MCP_WORKER_IMAGE", "mcp-worker:latest")
NAMESPACE = "backend"
OUTPUT_BASE = "/mnt/claude-output"


class RunRequest(BaseModel):
    prompt: str
    num_agents: int = 1


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run_git(args: list[str], cwd: str | None = None) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def setup_run_repo(group_id: str, num_agents: int) -> list[dict]:
    """Create a run directory, init a git repo, and create one worktree per agent.

    Directory layout:
        /mnt/claude-output/run-<group_id>/
            repo/              <- main git repository
            agent-0/           <- worktree on branch agent-0
            agent-1/           <- worktree on branch agent-1
            ...

    Returns a list of dicts with agent_id, branch, and worktree_path.
    """
    run_dir = os.path.join(OUTPUT_BASE, f"run-{group_id}")
    repo_dir = os.path.join(run_dir, "repo")
    os.makedirs(repo_dir, exist_ok=True)

    # 1. Init the repository
    _run_git(["init"], cwd=repo_dir)
    _run_git(["config", "user.email", "agent@claude.local"], cwd=repo_dir)
    _run_git(["config", "user.name", "Claude Agent"], cwd=repo_dir)

    # 2. Create an initial commit so branches can be created
    readme_path = os.path.join(repo_dir, "README.md")
    with open(readme_path, "w") as f:
        f.write(f"# Run {group_id}\n\nMulti-agent Claude MCP run.\n")
    _run_git(["add", "."], cwd=repo_dir)
    _run_git(["commit", "-m", "Initial commit"], cwd=repo_dir)

    # 3. Create a worktree + branch per agent
    worktrees: list[dict] = []
    for i in range(num_agents):
        branch_name = f"agent-{i}"
        worktree_path = os.path.join(run_dir, f"agent-{i}")
        _run_git(
            ["worktree", "add", "-b", branch_name, worktree_path],
            cwd=repo_dir,
        )
        logger.info("Created worktree %s on branch %s", worktree_path, branch_name)
        worktrees.append(
            {
                "agent_id": i,
                "branch": branch_name,
                "worktree_path": worktree_path,
            }
        )

    return worktrees


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/run")
async def run_agents(req: RunRequest):
    if not req.prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
    if req.num_agents < 1 or req.num_agents > 10:
        raise HTTPException(
            status_code=400, detail="Number of agents must be between 1 and 10"
        )

    job_group_id = uuid.uuid4().hex[:8]

    # --- 1. Create run folder, init git repo, create worktrees ---
    try:
        worktrees = setup_run_repo(job_group_id, req.num_agents)
    except subprocess.CalledProcessError as exc:
        logger.error("Git setup failed: %s\nstderr: %s", exc, exc.stderr)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set up git worktrees: {exc.stderr}",
        )

    # --- 2. Launch one K8s Job per agent, passing worktree info ---
    job_names = []
    for wt in worktrees:
        job_name = f"mcp-worker-{job_group_id}-{wt['agent_id']}"
        job = _build_job(
            name=job_name,
            prompt=req.prompt,
            agent_id=wt["agent_id"],
            group_id=job_group_id,
            branch=wt["branch"],
            worktree_path=wt["worktree_path"],
        )
        batch_v1.create_namespaced_job(namespace=NAMESPACE, body=job)
        job_names.append(job_name)

    return {"job_group_id": job_group_id, "jobs": job_names}


@app.get("/api/status/{job_group_id}")
async def get_status(job_group_id: str):
    jobs = batch_v1.list_namespaced_job(
        namespace=NAMESPACE, label_selector=f"job-group={job_group_id}"
    )

    results = []
    for job in jobs.items:
        succeeded = job.status.succeeded or 0
        failed = job.status.failed or 0
        active = job.status.active or 0

        if succeeded > 0:
            status = "completed"
        elif failed > 0:
            status = "failed"
        elif active > 0:
            status = "running"
        else:
            status = "pending"

        results.append(
            {
                "name": job.metadata.name,
                "status": status,
                "start_time": (
                    str(job.status.start_time) if job.status.start_time else None
                ),
                "completion_time": (
                    str(job.status.completion_time)
                    if job.status.completion_time
                    else None
                ),
            }
        )

    return {"job_group_id": job_group_id, "jobs": results}


@app.get("/api/results/{job_name}")
async def get_results(job_name: str):
    pods = core_v1.list_namespaced_pod(
        namespace=NAMESPACE, label_selector=f"job-name={job_name}"
    )

    logs = []
    for pod in pods.items:
        try:
            log = core_v1.read_namespaced_pod_log(
                name=pod.metadata.name, namespace=NAMESPACE
            )
            logs.append({"pod": pod.metadata.name, "log": log})
        except client.exceptions.ApiException as e:
            logs.append({"pod": pod.metadata.name, "error": str(e)})

    return {"job_name": job_name, "logs": logs}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# K8s Job builder
# ---------------------------------------------------------------------------

def _build_job(
    name: str,
    prompt: str,
    agent_id: int,
    group_id: str,
    branch: str,
    worktree_path: str,
) -> client.V1Job:
    """Build a K8s Job spec for an MCP worker.

    The worker receives the worktree path and branch via env vars so it can:
    - use the worktree as its working directory
    - commit all changes to the assigned branch when done
    """
    container = client.V1Container(
        name="mcp-worker",
        image=MCP_WORKER_IMAGE,
        image_pull_policy="IfNotPresent",
        env=[
            client.V1EnvVar(name="AGENT_PROMPT", value=prompt),
            client.V1EnvVar(name="AGENT_ID", value=str(agent_id)),
            client.V1EnvVar(name="JOB_GROUP_ID", value=group_id),
            client.V1EnvVar(name="AGENT_BRANCH", value=branch),
            client.V1EnvVar(name="AGENT_WORKTREE_PATH", value=worktree_path),
            client.V1EnvVar(
                name="ANTHROPIC_API_KEY",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="anthropic-api-key", key="api-key"
                    )
                ),
            ),
        ],
        volume_mounts=[
            client.V1VolumeMount(
                name="claude-output",
                mount_path=OUTPUT_BASE,
            )
        ],
        resources=client.V1ResourceRequirements(
            requests={"memory": "1Gi", "cpu": "1"},
            limits={"memory": "2Gi", "cpu": "2"},
        ),
        security_context=client.V1SecurityContext(
            run_as_non_root=True,
            run_as_user=1000,
            allow_privilege_escalation=False,
        ),
    )

    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(
            labels={"app": "mcp-worker", "job-group": group_id}
        ),
        spec=client.V1PodSpec(
            containers=[container],
            restart_policy="Never",
            volumes=[
                client.V1Volume(
                    name="claude-output",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name="claude-output-pvc",
                    ),
                )
            ],
        ),
    )

    spec = client.V1JobSpec(
        template=template,
        backoff_limit=0,
        ttl_seconds_after_finished=3600,
    )

    return client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=NAMESPACE,
            labels={"app": "mcp-worker", "job-group": group_id},
        ),
        spec=spec,
    )
