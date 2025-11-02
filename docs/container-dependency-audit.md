# Container Dependency Audit

## Executive Summary
- Objective: surface Python dependencies that bloat the container image and highlight unused pins in `requirements.txt`.
- Key actions: pruned unused data-science/model-conversion stacks and dormant client SDKs, trimming several hundred MB from the base image.
- Remaining hotspots: `torch`, `transformers`, `sentence-transformers`, and Google cloud analytics clients continue to dominate install size; treat them as critical dependencies with dedicated image layers and monitoring.

## Large Footprint Dependencies Actively Imported
| Package | Approx. install footprint* | Import touchpoints | Notes |
| --- | --- | --- | --- |
| `torch>=2.6` | ~2.0 GB (CPU wheel) | `erieiron_ml/gpu_utils.py:8`, `erieiron_autonomous_agent/utils/codegen_utils.py:10` | Required for vector math and GPU helpers; dominates layer size and triggers CUDA/MPS checks.
| `transformers` | ~0.5 GB incl. deps/model cache | `erieiron_autonomous_agent/utils/codegen_utils.py:12` | Pulls in tokenizers and model configs; `Dockerfile` pre-downloads `bert-base-uncased` and `all-MiniLM-L6-v2` (Dockerfile:58-60).
| `sentence-transformers==2.2.2` | ~100 MB plus model weights | `erieiron_common/chat_engine/language_utils.py:117`, `erieiron_autonomous_agent/coding_agents/self_driving_coder_agent_tofu.py:3074` | Wraps `torch` + `transformers`; ensures sentence embedding utilities but duplicates vector stacks.
| `numpy<2.0.0` | ~80 MB | `erieiron_common/common.py:31`, `erieiron_ml/gpu_utils.py:6`, `erieiron_autonomous_agent/utils/codegen_utils.py:11` | Core numerical backbone; also required transitively by `scikit-learn` and plotting helpers.
| `scikit-learn>=1.0.0` | ~75 MB | `erieiron_common/common.py:40` | Used exclusively for cosine similarity. Consider replacing with a pure NumPy implementation if only similarity is needed.
| `matplotlib` | ~35 MB | `erieiron_ml/gpu_utils.py:138` | Only used for plotting diagnostic curves; candidate for optional dependency to slim production image.
| `google-*` clients (`google-analytics-data`, `google-cloud-bigquery`, `google-generativeai`, `google-api-core`) | 20–60 MB each | `erieiron_common/gcp_utils.py:3-5`, `erieiron_common/llm_apis/gemini_chat_api.py:5` | Multiple Google SDKs installed; ensure all are needed in container, especially analytics vs. runtime paths.
| `boto3` + `botocore` | ~70 MB combined | Extensive AWS helpers e.g., `erieiron_common/aws_utils.py:22`, `erieiron_autonomous_agent/coding_agents/self_driving_coder_agent_tofu.py:15` | Heavy but essential for AWS orchestration; watch for duplicated session helpers to limit eager imports.

\*Footprints reflect typical Linux wheels before model downloads; cached model weights can add several hundred MB more.

## Follow-up Watchlist
### Potential optional candidates
- `matplotlib` — ship only where plotting is necessary; otherwise move behind an extras flag.
- `scikit-learn>=1.0.0` — evaluate migrating cosine similarity to `numpy` helpers and drop the ML toolkit if no other features rely on it.
- `torchvggish` — confirm whether audio feature extraction is still planned; removal would avoid ~120 MB of wheels and model artifacts.

### Likely indirect/runtime needs (document rationale before removal)
- `asgiref`, `sqlparse` — Django pulls these transitively; leaving them in requirements ensures deterministic deployments.
- `cryptography>=3.4` — required by `PyJWT[crypto]`; keep the explicit lower bound to satisfy security scans.
- `pillow`, `psycopg2-binary` — no direct imports, yet Pillow backs asset pipelines and psycopg2 powers the PostgreSQL backend via `agent_tools.get_django_settings_databases_conf()`.
- `gunicorn` — referenced in `docker-internal-startup-cmd.sh:24-47`; required for container entrypoint even without Python import.

## Recent Remediation
- Removed unused numerical, conversion, and auxiliary media libraries from `requirements.txt` to shrink the image footprint.
- Dropped dormant client SDK pins that were never imported in code paths.
- Normalized the `pygments` dependency to a single canonical entry.

## Recommendations
1. Create optional extras (e.g., `ml`, `viz`, `analytics`) so production images install only the dependencies they execute.
2. Continue monitoring large-model downloads in the Docker build (e.g., `Dockerfile:58-64`) and prefer runtime lazy fetches with shared caching when feasible.
3. Establish a periodic dependency audit (quarterly) to catch newly-unused packages before they increase the image size.
