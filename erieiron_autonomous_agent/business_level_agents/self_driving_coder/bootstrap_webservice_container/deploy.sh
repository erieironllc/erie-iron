#!/bin/bash

STACK_NAME="erie-iron-bootstrap"
TEMPLATE_FILE="cloudformation/stack.yaml"
REGION="us-west-2"
IMAGE_URI="123456789012.dkr.ecr.us-west-2.amazonaws.com/erie-iron:latest"

# Prompt for DB credentials
read -p "DB Username: " DB_USERNAME
read -s -p "DB Password: " DB_PASSWORD
echo

aws cloudformation deploy \
  --stack-name $STACK_NAME \
  --template-file $TEMPLATE_FILE \
  --capabilities CAPABILITY_NAMED_IAM \
  --region $REGION \
  --parameter-overrides \
    DBUsername="$DB_USERNAME" \
    DBPassword="$DB_PASSWORD" \
    ContainerImage="$IMAGE_URI"