#!/usr/bin/env bash
set -euo pipefail
export ENVIRONMENT_NAME=prod
export DEPLOY_ROLE=arn:aws:iam::782005355493:role/erieiron-infra-pipeline-prod-InfraDeployRole-YvERtezDIWx0

export VPC_ID=vpc-0eadb0bb19c420ec0
export IGW_ID=$(
  aws ec2 describe-internet-gateways \
    --filters Name=attachment.vpc-id,Values="$VPC_ID" \
    --query "InternetGateways[0].InternetGatewayId" \
    --output text
)

export LB_ARN=$(
  aws elbv2 describe-load-balancers \
    --names "webservice-alb-$ENVIRONMENT_NAME" \
    --query "LoadBalancers[0].LoadBalancerArn" \
    --output text 2>/dev/null || echo "None"
)
export TG_ARN=$(
  aws elbv2 describe-target-groups \
    --names "webservice-tg-$ENVIRONMENT_NAME" \
    --query "TargetGroups[0].TargetGroupArn" \
    --output text 2>/dev/null || echo "None"
)
export LISTENER_ARNS=$(
  aws elbv2 describe-listeners \
    --load-balancer-arn "$LB_ARN" \
    --query "Listeners[].ListenerArn" \
    --output text 2>/dev/null || echo ""
)

if [ -z "$LB_ARN" ] || [ "$LB_ARN" = "None" ]; then
  echo "No load balancer found for environment: $ENVIRONMENT_NAME, skipping ALB cleanup"
else
  for arn in ${LISTENER_ARNS}; do
    aws elbv2 delete-listener --listener-arn "$arn"
  done
  aws elbv2 delete-target-group --target-group-arn "$TG_ARN"
  aws elbv2 delete-load-balancer --load-balancer-arn "$LB_ARN"
  aws elbv2 wait load-balancers-deleted --load-balancer-arns "$LB_ARN"
fi

if [ -z "$TG_ARN" ] || [ "$TG_ARN" = "None" ]; then
  echo "No target group found for environment: $ENVIRONMENT_NAME, skipping TG cleanup"
else
  aws elbv2 delete-target-group --target-group-arn "$TG_ARN"
fi

if [[ -z "$IGW_ID" || "$IGW_ID" == "None" ]]; then
  echo "No Internet Gateway found for VPC: $VPC_ID, skipping IGW cleanup"
else
  echo "Detaching Internet Gateway $IGW_ID from VPC $VPC_ID"
  aws ec2 detach-internet-gateway --internet-gateway-id "$IGW_ID" --vpc-id "$VPC_ID"
  echo "Deleting Internet Gateway $IGW_ID"
  aws ec2 delete-internet-gateway --internet-gateway-id "$IGW_ID"
fi

echo "Attempting to delete ECS service-linked role AWSServiceRoleForECS (if it exists)"
aws iam delete-service-linked-role --role-name AWSServiceRoleForECS || echo "No ECS service-linked role found, skipping"

aws cloudformation delete-stack --stack-name "erieiron-infra-$ENVIRONMENT_NAME"
aws cloudformation wait stack-delete-complete --stack-name "erieiron-infra-$ENVIRONMENT_NAME"

#aws cloudformation deploy \
#  --template-file aws/cloudformation/cicd_pipeline.yml \
#  --stack-name "erieiron-infra-pipeline-$ENVIRONMENT_NAME" \
#  --capabilities CAPABILITY_NAMED_IAM \
#  --parameter-overrides \
#    GitHubOwner=jjschultz \
#    GitHubRepo=erieiron \
#    GitHubBranch=main \
#    EnvironmentName="$ENVIRONMENT_NAME"
#aws cloudformation wait stack-update-complete --stack-name "erieiron-infra-pipeline-$ENVIRONMENT_NAME"


aws cloudformation deploy \
  --template-file aws/cloudformation/runtime_environment.yml \
  --stack-name "erieiron-infra-$ENVIRONMENT_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides "EnvironmentName=$ENVIRONMENT_NAME"

aws cloudformation wait stack-update-complete --stack-name "erieiron-infra-$ENVIRONMENT_NAME"

aws cloudformation describe-stack-events --stack-name erieiron-infra-prod > ~/Desktop/describe-stack-events.output.json