FROM 782005355493.dkr.ecr.us-west-2.amazonaws.com/base-images:python-3.11-slim

# Build arguments and environment variables
ARG ERIEIRON_ENV=dev
ARG HTTP_LISTENER_PORT=8006
ARG CACHEBUST=1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HTTP_LISTENER_PORT=${HTTP_LISTENER_PORT} \
    DJANGO_SETTINGS_MODULE=settings \
    ERIEIRON_ENV=${ERIEIRON_ENV} \
    AWS_REGION=us-west-2

# Do not set DB_* environment variables at build time.
# Secrets and credentials will be provided securely at runtime via Secrets Manager.

# Install system dependencies including Node.js.
RUN apt-get update && apt-get install -y \
    gcc \
    git \
    postgresql-client \
    libpq-dev \
    curl \
    gettext-base \
    && curl -fsSL https://astral.sh/uv/install.sh | bash - \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:$PATH"

# Install AWS CLI and upgrade pip
RUN pip install awscli && pip install --upgrade pip
# Install AWS SAM CLI for SAM-based local invocations during tests
RUN pip install --no-cache-dir aws-sam-cli
# Verify SAM CLI is available on PATH (fail fast if missing)
RUN sam --version || (echo "SAM CLI not found on PATH after install" && exit 1)

# Create app directory and set working directory
WORKDIR /app

# Copy requirements and constraints files
COPY requirements.txt .

RUN pip install --no-cache-dir gunicorn

ARG ERIEIRON_PUBLIC_COMMON_SHA=manual
RUN echo "Using erieiron-public-common ref: $ERIEIRON_PUBLIC_COMMON_SHA"

#
# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set Hugging Face cache directories before model downloads
ENV HF_HOME=/usr/local/huggingface \
    TRANSFORMERS_CACHE=$HF_HOME \
    SENTENCE_TRANSFORMERS_HOME=$HF_HOME \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1

# Persist environment variables system-wide (optional hardening)
RUN echo 'export HF_HOME=/usr/local/huggingface' >> /etc/profile.d/hf_cache.sh && \
    echo 'export TRANSFORMERS_CACHE=/usr/local/huggingface' >> /etc/profile.d/hf_cache.sh && \
    echo 'export SENTENCE_TRANSFORMERS_HOME=/usr/local/huggingface' >> /etc/profile.d/hf_cache.sh

RUN mkdir -p $HF_HOME && chmod -R 755 $HF_HOME

# Verify environment variables and ensure cache directory exists
RUN echo "[build] HF_HOME=$HF_HOME" && \
    echo "[build] TRANSFORMERS_CACHE=$TRANSFORMERS_CACHE" && \
    echo "[build] SENTENCE_TRANSFORMERS_HOME=$SENTENCE_TRANSFORMERS_HOME" && \
    mkdir -p "$HF_HOME" && ls -la "$HF_HOME"

# Download and cache models at build time
RUN python - <<'PYCODE'
from transformers import AutoModel, AutoTokenizer
from sentence_transformers import SentenceTransformer
import os

cache_dir = os.environ.get("HF_HOME", "/usr/local/huggingface")
os.makedirs(cache_dir, exist_ok=True)

models = [
    "bert-base-uncased",
    "sentence-transformers/all-MiniLM-L6-v2",
]

for m in models:
    print(f"[build] Downloading and caching {m}")
    AutoModel.from_pretrained(m, cache_dir=cache_dir)
    AutoTokenizer.from_pretrained(m, cache_dir=cache_dir)

SentenceTransformer("all-MiniLM-L6-v2")
print("[build] Cache preloaded successfully")
PYCODE

# Inspect cache for confirmation
RUN ls -Rlh /usr/local/huggingface

# Keep cache volume for reuse at runtime
VOLUME /usr/local/huggingface

COPY . .

RUN chmod +x /app/docker-internal-startup-cmd.sh

# Expose listener port.
EXPOSE ${HTTP_LISTENER_PORT}

# Health check to ensure the application is responsive.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -fsSL "http://localhost:${HTTP_LISTENER_PORT}/health/" || exit 1

SHELL ["/bin/bash", "-c"]
CMD ["bash", "/app/docker-internal-startup-cmd.sh"]

# Indicate completion of Docker build.
RUN echo "Docker build complete for dynamic platform ($TARGETPLATFORM)."