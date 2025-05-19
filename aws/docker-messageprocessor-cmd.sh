#!/bin/bash
set -e
trap 'kill $(jobs -p) 2>/dev/null' EXIT  # Cleanup background processes on exit

ERIELAB_ENV="${ERIELAB_ENV:-default}_asset_processor"
export ERIELAB_ENV
echo "Starting message processor daemons..."
python3 manage.py migrate --noinput || { echo "migrate failed"; exit 1; }

if [[ "$MSG_PROCESSOR_MANUAL_CONFIG" == "true" ]]; then
  ERIELAB_ENV="$ERIELAB_ENV" python3 manage.py message_process_manager \
      --instance_id="${INSTANCE_ID}"
else
  # these are the defaults that are used when we scale up ec2 message processors
  # the bare metal servers pass in these values
  MSG_PROCESSOR_COUNT="${MSG_PROCESSOR_COUNT:-4}"
  THREADS_PER_PROCESS="${THREADS_PER_PROCESS:-3}"
  JOB_LIMITS_DEF="${JOB_LIMITS_DEF:-stem_separate_asset:2}"

  ERIELAB_ENV="$ERIELAB_ENV" python3 manage.py message_process_manager \
      --instance_id="${INSTANCE_ID}" \
      --job_limits_def="${JOB_LIMITS_DEF}" \
      --threads_per_process="${THREADS_PER_PROCESS}" \
      --process_count="${MSG_PROCESSOR_COUNT}"
fi

wait