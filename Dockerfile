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
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:$PATH"

 # Install AWS CLI and AWS SAM CLI using pip
ENV PIP_ROOT_USER_ACTION=ignore
RUN pip install awscli aws-sam-cli

# Create app directory and set working directory
WORKDIR /app

# Copy requirements and constraints files
COPY requirements.txt .

RUN pip install gunicorn

ARG ERIEIRON_PUBLIC_COMMON_SHA=manual
RUN echo "Using erieiron-public-common ref: $ERIEIRON_PUBLIC_COMMON_SHA"

#
# Install Python dependencies
RUN pip install -r requirements.txt

# Configure HuggingFace cache location; runtime will populate via S3 sync.
ENV HF_HOME=/usr/local/huggingface
ENV TRANSFORMERS_CACHE=${HF_HOME}
ENV SENTENCE_TRANSFORMERS_HOME=${HF_HOME}
ENV HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    TRANSFORMERS_OFFLINE=0 \
    HF_DATASETS_OFFLINE=0

RUN mkdir -p "$HF_HOME" && chmod -R 755 "$HF_HOME"

# Keep cache volume for reuse at runtime
VOLUME /usr/local/huggingface

COPY . .

RUN chmod +x /app/docker-internal-startup-cmd.sh

RUN npm install
RUN npm run compile-ui

# Expose listener port.
EXPOSE ${HTTP_LISTENER_PORT}

# Health check to ensure the application is responsive.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -fsSL "http://localhost:${HTTP_LISTENER_PORT}/health/" || exit 1

SHELL ["/bin/bash", "-c"]
CMD ["bash", "/app/docker-internal-startup-cmd.sh"]

# Indicate completion of Docker build.
RUN echo "Docker build complete for dynamic platform ($TARGETPLATFORM)."
