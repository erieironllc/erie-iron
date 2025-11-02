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

# Pre-download Hugging Face models to cache
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
RUN python -c "from transformers import AutoModel, AutoTokenizer; [AutoModel.from_pretrained(m) and AutoTokenizer.from_pretrained(m) for m in ['bert-base-uncased', 'sentence-transformers/all-MiniLM-L6-v2']]" || true

# Persist Hugging Face cache so models are available at runtime
ENV HF_HOME=/usr/local/huggingface
RUN mkdir -p $HF_HOME && \
    cp -r /root/.cache/huggingface/* $HF_HOME/ && \
    chmod -R 755 $HF_HOME

# Ensure runtime uses the same cache directory
ENV TRANSFORMERS_CACHE=$HF_HOME \
    SENTENCE_TRANSFORMERS_HOME=$HF_HOME \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1

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