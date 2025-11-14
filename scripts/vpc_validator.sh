#!/bin/bash
set -euo pipefail

# Ensure Django has access to the database credentials secret when invoked non-interactively
export RDS_SECRET_ARN="${RDS_SECRET_ARN:-arn:aws:secretsmanager:us-west-2:782005355493:secret:rds!db-2b88e9c3-dde5-470b-9b89-87422dd09b6d-bT0oU1}"

bash ./scripts/bootstrap_target_account.sh curators dev
python manage.py vpc_validation
