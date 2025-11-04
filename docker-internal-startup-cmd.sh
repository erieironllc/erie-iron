#!/usr/bin/env bash
set -euo pipefail

# Print environment for debugging visibility.
env

PORT="${HTTP_LISTENER_PORT:-8006}"
export PYTHONPATH="/app:${PYTHONPATH:-}"

python manage.py collectstatic --noinput || { echo "collectstatic failed"; exit 1; }

GUNICORN_WORKERS=4
GUNICORN_THREADS=8
GUNICORN_TIMEOUT=60

echo "Starting Gunicorn..."
echo "[webcontainer startup] INFO Starting gunicorn erieiron_config.wsgi on port ${PORT}"

gunicorn \
   --workers $GUNICORN_WORKERS \
   --threads $GUNICORN_THREADS \
   --timeout $GUNICORN_TIMEOUT \
   --graceful-timeout 15 \
   --preload \
   --bind 0.0.0.0:${PORT} \
   erieiron_config.wsgi:application --log-file=-
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
