#!/usr/bin/env bash
set -euo pipefail
export ENVIRONMENT_NAME=prod

# Deploy the infra pipeline stack
aws cloudformation deploy \
  --template-file aws/cloudformation/infra-pipeline.yml \
  --stack-name "erieiron-infra-pipeline-$ENVIRONMENT_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    GitHubOwner=jjschultz \
    GitHubRepo=erieiron \
    GitHubBranch=main \
    EnvironmentName="$ENVIRONMENT_NAME"
aws cloudformation wait stack-update-complete --stack-name "erieiron-infra-pipeline-$ENVIRONMENT_NAME"
aws codepipeline start-pipeline-execution --name "erieiron-infra-pipeline-$ENVIRONMENT_NAME"

# Deploy the app pipeline stack
aws cloudformation deploy \
  --template-file aws/cloudformation/app-pipeline.yml \
  --stack-name "erieiron-app-pipeline-$ENVIRONMENT_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    GitHubOwner=jjschultz \
    GitHubRepo=erieiron \
    GitHubBranch=main \
    EnvironmentName="$ENVIRONMENT_NAME"
aws cloudformation wait stack-update-complete --stack-name "erieiron-app-pipeline-$ENVIRONMENT_NAME"
aws codepipeline start-pipeline-execution --name "erieiron-app-pipeline-$ENVIRONMENT_NAME"