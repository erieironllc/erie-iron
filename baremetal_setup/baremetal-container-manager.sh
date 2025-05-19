#!/bin/bash
exec > >(logger -t baremetal-container-manager) 2>&1

# run by crontab every minute
# lives in /usr/local/bin/

set -euo pipefail

region="$(aws configure get region)"
account_id="$(aws sts get-caller-identity --query "Account" --output text)"
repository_name="erielab-messageprocessor"
image_tag="latest"

container_name="message-processor"
current_image_id=$(docker inspect --format='{{.Image}}' "$container_name")

running_digest=$(docker image inspect \
  "$current_image_id" \
  --format='{{index .RepoDigests 0}}' 2>/dev/null \
  | cut -d'@' -f2)
# logger -t baremetal-container-manager "DEBUG: running_digest='$running_digest'"

latest_digest=$(aws ecr describe-images \
  --repository-name "$repository_name" \
  --region "$region" \
  --query "sort_by(imageDetails[?imageTags && contains(imageTags, 'latest')], &imagePushedAt)[-1].imageDigest" \
  --output text | grep -v '^None$' | tr -d '\r' | xargs)

# logger -t baremetal-container-manager "DEBUG: latest_digest='$latest_digest'"

if [ -z "$latest_digest" ]; then
  logger -t baremetal-container-manager "Failed to retrieve the latest image digest for $repository_name"
  exit 1
fi


if [ -z "$running_digest" ]; then
  logger -t baremetal-container-manager "Failed to retrieve the running image's digest for $current_image_id"
  exit 1
fi

# Compare and update if they differ
if [ "$latest_digest" != "$running_digest" ]; then
  logger -t baremetal-container-manager "New image detected.  current is $running_digest . Updating container to $latest_digest"
  image_uri="$account_id.dkr.ecr.$region.amazonaws.com/$repository_name:$image_tag"

  systemctl daemon-reload
  systemctl restart docker-message-processor.service

  logger -t baremetal-container-manager "Container updated successfully to $latest_digest"
fi