#!/usr/bin/env bash
set -euo pipefail

GUNICORN_WORKERS=4
GUNICORN_THREADS=8
GUNICORN_TIMEOUT=60


# Print environment for debugging visibility.
env

PORT="${HTTP_LISTENER_PORT:-8006}"
export PYTHONPATH="/app:${PYTHONPATH:-}"

python manage.py collectstatic --noinput 2>&1 | grep -v "Found another file with the destination path" &

echo "Starting message processor daemons for env ${ERIEIRON_ENV}..."
python manage.py message_processor_daemon \
  --max_threads=8 \
  --env="${ERIEIRON_ENV}" \
  --suppress_timing_messages=False &

echo "Starting Gunicorn..."
echo "[webcontainer startup] INFO Starting gunicorn erieiron_config.wsgi on port ${PORT}"
exec gunicorn \
   --workers $GUNICORN_WORKERS \
   --threads $GUNICORN_THREADS \
   --timeout $GUNICORN_TIMEOUT \
   --graceful-timeout 15 \
   --preload \
   --bind 0.0.0.0:${PORT} \
   erieiron_config.wsgi:application --log-file=-
