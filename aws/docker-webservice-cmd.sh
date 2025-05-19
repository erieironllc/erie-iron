#!/bin/bash
set -e
trap 'kill $(jobs -p) 2>/dev/null' EXIT  # Cleanup background processes on exit

python3 manage.py collectstatic --noinput || { echo "collectstatic failed"; exit 1; }

GUNICORN_WORKERS=4
GUNICORN_THREADS=4
GUNICORN_TIMEOUT=100
GUNICORN_PORT=8001

echo "Starting Gunicorn..."
gunicorn \
   --workers $GUNICORN_WORKERS \
   --threads $GUNICORN_THREADS \
   --timeout $GUNICORN_TIMEOUT \
   --bind 0.0.0.0:$GUNICORN_PORT \
   webservice.wsgi.wsgi:application --log-file=-

wait