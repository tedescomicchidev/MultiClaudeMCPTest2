import os

from flask import Flask, render_template, request, jsonify
import requests as http_requests

app = Flask(__name__)

ORCHESTRATOR_URL = os.environ.get(
    "ORCHESTRATOR_URL", "http://orchestrator.backend.svc.cluster.local:8080"
)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/run", methods=["POST"])
def run():
    data = request.get_json()
    prompt = data.get("prompt", "")
    num_agents = int(data.get("num_agents", 1))
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400
    if num_agents < 1 or num_agents > 10:
        return jsonify({"error": "Number of agents must be between 1 and 10"}), 400
    try:
        resp = http_requests.post(
            f"{ORCHESTRATOR_URL}/api/run",
            json={"prompt": prompt, "num_agents": num_agents},
            timeout=30,
        )
        return jsonify(resp.json()), resp.status_code
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to contact orchestrator: {str(e)}"}), 502


@app.route("/api/status/<job_group_id>")
def status(job_group_id):
    try:
        resp = http_requests.get(
            f"{ORCHESTRATOR_URL}/api/status/{job_group_id}", timeout=10
        )
        return jsonify(resp.json()), resp.status_code
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to contact orchestrator: {str(e)}"}), 502


@app.route("/api/results/<job_name>")
def results(job_name):
    try:
        resp = http_requests.get(
            f"{ORCHESTRATOR_URL}/api/results/{job_name}", timeout=10
        )
        return jsonify(resp.json()), resp.status_code
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to contact orchestrator: {str(e)}"}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
