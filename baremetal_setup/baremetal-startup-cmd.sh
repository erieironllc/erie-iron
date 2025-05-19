#!/bin/bash
exec > >(logger -t baremetal-container-manager) 2>&1

aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin 471112823728.dkr.ecr.us-west-2.amazonaws.com
docker pull 471112823728.dkr.ecr.us-west-2.amazonaws.com/erielab-messageprocessor:latest

docker stop message-processor || true
docker rm message-processor || true

docker run -d \
  --name message-processor \
  -e INSTANCE_ID=$(hostname) \
  -e MSG_PROCESSOR_MANUAL_CONFIG="true" \
  -e AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id) \
  -e AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key) \
  -e AWS_REGION=$(aws configure get region) \
  --gpus all \
  --restart unless-stopped \
  --log-driver=awslogs \
  --log-opt awslogs-region=us-west-2 \
  --log-opt awslogs-group=erielab-message-processor \
  --log-opt awslogs-create-group=true \
  471112823728.dkr.ecr.us-west-2.amazonaws.com/erielab-messageprocessor:latest
