#!/bin/bash

source ./env/bin/activate

export DJANGO_SETTINGS_MODULE=settings
export LOCAL_DB_NAME=""
export ERIEIRON_ENV=production
export ERIEIRON_DB_HOST="kfqxw-db.cfmokeqce4va.us-west-2.rds.amazonaws.com"
export ERIEIRON_DB_NAME="appdb"
export AWS_PROFILE=erie-iron
export RDS_SECRET_ARN=arn:aws:secretsmanager:us-west-2:782005355493:secret:rds!db-2b88e9c3-dde5-470b-9b89-87422dd09b6d-bT0oU1
export AWS_DEFAULT_REGION=us-west-2
export MESSAGE_TYPES="-DESIGN_WORK_REQUESTED,-CODING_WORK_REQUESTED"

python manage.py message_processor_daemon \
  --retry_failed=True \
  --debug_output=True \
  --max_threads=8 \
  --env=${ERIEIRON_ENV} \
  --suppress_timing_messages=True
