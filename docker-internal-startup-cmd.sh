#!/usr/bin/env bash
set -euo pipefail

# Print environment for debugging visibility.
env

PORT="${HTTP_LISTENER_PORT:-8006}"
export PYTHONPATH="/app:${PYTHONPATH:-}"

HF_CACHE_DIR="${HF_HOME:-/usr/local/huggingface}"
if [[ -n "${HF_MODEL_CACHE_S3_URI:-}" ]]; then
  echo "[webcontainer startup] INFO syncing HuggingFace cache from ${HF_MODEL_CACHE_S3_URI}"
  mkdir -p "${HF_CACHE_DIR}"
  aws s3 sync "${HF_MODEL_CACHE_S3_URI}" "${HF_CACHE_DIR}" --only-show-errors
  echo "[webcontainer startup] INFO HuggingFace cache sync complete"
else
  echo "[webcontainer startup] INFO HF_MODEL_CACHE_S3_URI not set; skipping HuggingFace cache sync"
fi

# Memory and OOM logging
echo "[webcontainer startup] INFO Monitoring dmesg for OOM kills..."
( dmesg --follow --human | grep --line-buffered -i "killed process" ) &
DMESG_PID=$!

echo "[webcontainer startup] INFO Starting memory monitor..."
( while true; do
    echo "[memcheck] $(date): $(awk '/MemAvailable/ {print $2 \" kB available\"}' /proc/meminfo)"
    sleep 5
done ) &
MEM_MONITOR_PID=$!

trap "kill $DMESG_PID $MEM_MONITOR_PID 2>/dev/null || true" EXIT

echo "[webcontainer startup] INFO Starting gunicorn erieiron_config.wsgi on port ${PORT}"
gunicorn erieiron_config.wsgi:application \
  --bind 0.0.0.0:${PORT} \
  --workers 1 \
  --threads 2 \
  --timeout 90 \
  --max-requests 100 \
  --max-requests-jitter 10 \
  --log-level info &
GUNICORN_PID=$!

echo "[webcontainer startup] INFO gunicorn started with PID ${GUNICORN_PID}"

forward_signal() {
    if kill -0 "${GUNICORN_PID}" >/dev/null 2>&1; then
        echo "[webcontainer startup] INFO Forwarding signal to gunicorn (PID ${GUNICORN_PID})"
        kill -TERM "${GUNICORN_PID}"
    fi
}
trap forward_signal TERM INT

cleanup() {
    if kill -0 "${GUNICORN_PID}" >/dev/null 2>&1; then
        echo "[webcontainer startup] INFO Cleaning up gunicorn (PID ${GUNICORN_PID})"
        kill "${GUNICORN_PID}" || true
        wait "${GUNICORN_PID}" || true
    fi
}
trap cleanup EXIT

echo "[webcontainer startup] Waiting for health endpoint..."
for attempt in {1..12}; do
echo "DUDE AWESOM"
    sleep 5
    if curl -fsSL "http://127.0.0.1:${PORT}/health/" >/dev/null 2>&1; then
        echo "[webcontainer startup] INFO internal docker healthcheck succeeded"
        wait "${GUNICORN_PID}"
        exit 0
    fi
done

echo "[webcontainer startup]  ERROR internal docker healthcheck failed.  Killing gunicorn (PID ${GUNICORN_PID})"
kill "${GUNICORN_PID}" || true
wait "${GUNICORN_PID}" || true
exit 1
