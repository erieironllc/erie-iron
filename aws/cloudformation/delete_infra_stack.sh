#!/bin/bash

set -euo pipefail

STACK_NAME="erieiron-infra-prod"
RESOURCE_NAME="CodePipelineServiceRole"

echo "Fetching physical resource ID for $RESOURCE_NAME from stack $STACK_NAME..."
ROLE_NAME=$(aws cloudformation describe-stack-resources \
  --stack-name "$STACK_NAME" \
  --logical-resource-id "$RESOURCE_NAME" \
  --query "StackResources[0].PhysicalResourceId" \
  --output text)

if [ -z "$ROLE_NAME" ]; then
  echo "ERROR: Role not found. Exiting."
  exit 1
fi

echo "Found IAM role: $ROLE_NAME"

echo "Checking if role still exists in IAM..."
if ! aws iam get-role --role-name "$ROLE_NAME" &>/dev/null; then
  echo "Role $ROLE_NAME does not exist in IAM. Skipping role deletion steps."
else
  echo "Detaching managed policies..."
  MANAGED_POLICIES=$(aws iam list-attached-role-policies --role-name "$ROLE_NAME" \
    --query "AttachedPolicies[].PolicyArn" --output text)

  for POLICY_ARN in $MANAGED_POLICIES; do
    echo "Detaching $POLICY_ARN..."
    aws iam detach-role-policy --role-name "$ROLE_NAME" --policy-arn "$POLICY_ARN"
  done

  echo "Deleting inline policies..."
  INLINE_POLICIES=$(aws iam list-role-policies --role-name "$ROLE_NAME" \
    --query "PolicyNames[]" --output text)

  for POLICY_NAME in $INLINE_POLICIES; do
    echo "Deleting inline policy $POLICY_NAME..."
    aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name "$POLICY_NAME"
  done

  echo "Deleting role $ROLE_NAME..."
  aws iam delete-role --role-name "$ROLE_NAME"
fi

# Delete the CloudFormation stack
echo "Deleting CloudFormation stack $STACK_NAME..."
aws cloudformation delete-stack --stack-name "$STACK_NAME"

# Wait for deletion to complete
echo "Waiting for stack deletion to complete..."
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"

echo "✅ Stack and role cleanup complete!"