#!/bin/bash
set -euo pipefail

# Ensure Django has access to the database credentials secret when invoked non-interactively
export RDS_SECRET_ARN="${RDS_SECRET_ARN:-arn:aws:secretsmanager:us-west-2:782005355493:secret:rds!db-ae150e73-c5a4-4572-a26d-bf3b1d191f91-JTuYuO}"

bash ./scripts/bootstrap_target_account.sh curators dev
python manage.py vpc_validation
