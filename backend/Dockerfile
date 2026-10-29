FROM python:3.11-slim

# Install system dependencies (git for repo/PR workflows, docker.io for container sandboxing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    build-essential \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency definition and install
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy source tree
COPY backend /app/backend
COPY sandbox_runner /app/sandbox_runner
COPY demo_target_repo /app/demo_target_repo
COPY BlastRadiusReport.json MigrationDelta.json Stage3MigrationReport.json /app/

# Environment settings
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    ALLOW_LOCAL_REPOS=0

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
