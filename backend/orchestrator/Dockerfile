FROM python:3.12-slim

# Git is required for creating repos and worktrees
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global init.defaultBranch main

RUN useradd -m -u 1000 -s /bin/bash orchestrator

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

USER orchestrator

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
