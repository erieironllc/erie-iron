#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# ==================== Configuration ====================

# AWS Configuration
AWS_REGION="us-west-2"                   # Replace with your AWS region
AWS_ACCOUNT_ID="471112823728"            # Replace with your AWS account ID

# ECR Repository Configuration
ECR_REPO_NAME="cuda-11.8.0-cudnn8-runtime-ubuntu20.04"  # Desired ECR repository name

# Docker Image Configuration
DOCKER_HUB_IMAGE="nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu20.04"  # Source image
IMAGE_TAG="11.8.0-cudnn8-runtime-ubuntu20.04"                     # Tag for ECR

# =========================================================

# Function to check if required commands are installed
command_exists () {
    command -v "$1" >/dev/null 2>&1
}

# Check for required commands
for cmd in aws docker; do
    if ! command_exists $cmd; then
        echo "Error: '$cmd' is not installed. Please install it and try again."
        exit 1
    fi
done

# Construct ECR Repository URI
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"

# Check if ECR repository exists; if not, create it
echo "Checking if ECR repository '${ECR_REPO_NAME}' exists..."
if ! aws ecr describe-repositories --repository-names "${ECR_REPO_NAME}" --region "${AWS_REGION}" >/dev/null 2>&1; then
    echo "Repository does not exist. Creating ECR repository '${ECR_REPO_NAME}'..."
    aws ecr create-repository --repository-name "${ECR_REPO_NAME}" --region "${AWS_REGION}" >/dev/null
    echo "ECR repository '${ECR_REPO_NAME}' created successfully."
else
    echo "ECR repository '${ECR_REPO_NAME}' already exists."
fi

# Authenticate Docker to ECR
echo "Authenticating Docker to AWS ECR..."
aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
echo "Docker authenticated to ECR successfully."

# Pull the Docker image from Docker Hub
echo "Pulling Docker image '${DOCKER_HUB_IMAGE}' from Docker Hub..."
docker pull "${DOCKER_HUB_IMAGE}"
echo "Docker image '${DOCKER_HUB_IMAGE}' pulled successfully."

# Tag the Docker image for ECR
ECR_IMAGE="${ECR_URI}:${IMAGE_TAG}"
echo "Tagging image '${DOCKER_HUB_IMAGE}' as '${ECR_IMAGE}'..."
docker tag "${DOCKER_HUB_IMAGE}" "${ECR_IMAGE}"
echo "Image tagged successfully."

# Push the Docker image to ECR
echo "Pushing image '${ECR_IMAGE}' to ECR..."
docker push "${ECR_IMAGE}"
echo "Image pushed to ECR successfully."

# Summary
echo "✅ Docker image '${DOCKER_HUB_IMAGE}' is now available in ECR as '${ECR_IMAGE}'."