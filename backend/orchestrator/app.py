import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from kubernetes import client, config
from pydantic import BaseModel

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


class RunRequest(BaseModel):
    prompt: str
    num_agents: int = 1


@app.post("/api/run")
async def run_agents(req: RunRequest):
    if not req.prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
    if req.num_agents < 1 or req.num_agents > 10:
        raise HTTPException(
            status_code=400, detail="Number of agents must be between 1 and 10"
        )

    job_group_id = uuid.uuid4().hex[:8]
    job_names = []

    for i in range(req.num_agents):
        job_name = f"mcp-worker-{job_group_id}-{i}"
        job = _build_job(job_name, req.prompt, i, job_group_id)
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


def _build_job(
    name: str, prompt: str, agent_id: int, group_id: str
) -> client.V1Job:
    container = client.V1Container(
        name="mcp-worker",
        image=MCP_WORKER_IMAGE,
        image_pull_policy="IfNotPresent",
        env=[
            client.V1EnvVar(name="AGENT_PROMPT", value=prompt),
            client.V1EnvVar(name="AGENT_ID", value=str(agent_id)),
            client.V1EnvVar(name="JOB_GROUP_ID", value=group_id),
            client.V1EnvVar(
                name="ANTHROPIC_API_KEY",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="anthropic-api-key", key="api-key"
                    )
                ),
            ),
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
