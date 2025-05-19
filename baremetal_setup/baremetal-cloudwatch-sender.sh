#!/bin/bash

# AWS Configuration
AWS_REGION="us-west-2"
LOG_GROUP="collaya-baremetal-logs"
LOG_STREAM="system-logs"
ACCESS_KEY=SET_ACCESS_KEY
SECRET_KEY=SET_SECRET_KEY

# Set AWS credentials
export AWS_ACCESS_KEY_ID="$ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$SECRET_KEY"
export AWS_DEFAULT_REGION="$AWS_REGION"

# Create log group if it doesn't exist
aws logs create-log-group --log-group-name "$LOG_GROUP" 2>/dev/null || true

# Create log stream if it doesn't exist
aws logs create-log-stream --log-group-name "$LOG_GROUP" --log-stream-name "$LOG_STREAM" 2>/dev/null || true

# Get sequence token if stream exists
SEQUENCE_TOKEN=$(aws logs describe-log-streams --log-group-name "$LOG_GROUP" --log-stream-name-prefix "$LOG_STREAM" --query 'logStreams[0].uploadSequenceToken' --output text)

# Format log events for CloudWatch
TIMESTAMP=$(date +%s)000
EVENTS_JSON="["

# Take last 20 lines from syslog to avoid size limits
while IFS= read -r line; do
  if [ -n "$line" ]; then
    EVENTS_JSON+="{ \"timestamp\": $TIMESTAMP, \"message\": \"$(echo "$line" | sed 's/"/\\"/g')\" },"
    # Increment timestamp slightly to maintain order
    TIMESTAMP=$((TIMESTAMP+1))
  fi
done < <(tail -n 20 /var/log/syslog)

# Remove trailing comma and close JSON array
EVENTS_JSON="${EVENTS_JSON%,}]"

# Prepare the put-log-events command
if [ "$SEQUENCE_TOKEN" == "None" ] || [ -z "$SEQUENCE_TOKEN" ]; then
  aws logs put-log-events \
    --log-group-name "$LOG_GROUP" \
    --log-stream-name "$LOG_STREAM" \
    --log-events "$EVENTS_JSON"
else
  aws logs put-log-events \
    --log-group-name "$LOG_GROUP" \
    --log-stream-name "$LOG_STREAM" \
    --sequence-token "$SEQUENCE_TOKEN" \
    --log-events "$EVENTS_JSON"
fi

# Add GPU metrics as a separate log entry
if command -v nvidia-smi &> /dev/null; then
  # Create GPU log stream if needed
  GPU_STREAM="gpu-metrics"
  aws logs create-log-stream --log-group-name "$LOG_GROUP" --log-stream-name "$GPU_STREAM" 2>/dev/null || true

  # Get GPU info and format for CloudWatch
  GPU_INFO=$(nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.used,memory.free,memory.total --format=csv)
  GPU_TIMESTAMP=$(date +%s)000

  # Create JSON event with GPU data
  GPU_EVENTS_JSON="["
  while IFS= read -r line; do
    if [ -n "$line" ]; then
      GPU_EVENTS_JSON+="{ \"timestamp\": $GPU_TIMESTAMP, \"message\": \"$(echo "$line" | sed 's/"/\\"/g')\" },"
      GPU_TIMESTAMP=$((GPU_TIMESTAMP+1))
    fi
  done < <(echo "$GPU_INFO")
  GPU_EVENTS_JSON="${GPU_EVENTS_JSON%,}]"

  # Get sequence token for GPU stream
  GPU_SEQUENCE_TOKEN=$(aws logs describe-log-streams --log-group-name "$LOG_GROUP" --log-stream-name-prefix "$GPU_STREAM" --query 'logStreams[0].uploadSequenceToken' --output text)

  # Upload GPU metrics
  if [ "$GPU_SEQUENCE_TOKEN" == "None" ] || [ -z "$GPU_SEQUENCE_TOKEN" ]; then
    aws logs put-log-events \
      --log-group-name "$LOG_GROUP" \
      --log-stream-name "$GPU_STREAM" \
      --log-events "$GPU_EVENTS_JSON"
  else
    aws logs put-log-events \
      --log-group-name "$LOG_GROUP" \
      --log-stream-name "$GPU_STREAM" \
      --sequence-token "$GPU_SEQUENCE_TOKEN" \
      --log-events "$GPU_EVENTS_JSON"
  fi
fi


# Add Docker container logs
if docker ps | grep -q "message-processor"; then
  # Create container log stream if needed
  CONTAINER_STREAM="container-logs"
  aws logs create-log-stream --log-group-name "$LOG_GROUP" --log-stream-name "$CONTAINER_STREAM" 2>/dev/null || true

  # Get Docker logs
  CONTAINER_LOGS=$(docker logs --tail 20 message-processor 2>&1)
  CONTAINER_TIMESTAMP=$(date +%s)000

  # Create JSON event with container data
  CONTAINER_EVENTS_JSON="["
  while IFS= read -r line; do
    if [ -n "$line" ]; then
      CONTAINER_EVENTS_JSON+="{ \"timestamp\": $CONTAINER_TIMESTAMP, \"message\": \"$(echo "$line" | sed 's/"/\\"/g')\" },"
      CONTAINER_TIMESTAMP=$((CONTAINER_TIMESTAMP+1))
    fi
  done < <(echo "$CONTAINER_LOGS")
  CONTAINER_EVENTS_JSON="${CONTAINER_EVENTS_JSON%,}]"

  # Get sequence token for container stream
  CONTAINER_SEQUENCE_TOKEN=$(aws logs describe-log-streams --log-group-name "$LOG_GROUP" --log-stream-name-prefix "$CONTAINER_STREAM" --query 'logStreams[0].uploadSequenceToken' --output text)

  # Upload container logs
  if [ "$CONTAINER_SEQUENCE_TOKEN" == "None" ] || [ -z "$CONTAINER_SEQUENCE_TOKEN" ]; then
    aws logs put-log-events \
      --log-group-name "$LOG_GROUP" \
      --log-stream-name "$CONTAINER_STREAM" \
      --log-events "$CONTAINER_EVENTS_JSON"
  else
    aws logs put-log-events \
      --log-group-name "$LOG_GROUP" \
      --log-stream-name "$CONTAINER_STREAM" \
      --sequence-token "$CONTAINER_SEQUENCE_TOKEN" \
      --log-events "$CONTAINER_EVENTS_JSON"
  fi
fi