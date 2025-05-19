#!/bin/bash

# Script Name: recreate_docker_run.sh
# Description: Reconstructs the original 'docker run' command for an existing container.
# Usage: ./recreate_docker_run.sh <container_name_or_id>

set -e

# Function to display usage
usage() {
    echo "Usage: $0 <container_name_or_id>"
    exit 1
}

# Check if container name or ID is provided
if [ $# -ne 1 ]; then
    usage
fi

CONTAINER=$1

# Verify that the container exists
if ! docker ps -a --format '{{.ID}} {{.Names}}' | grep -wq "$CONTAINER"; then
    echo "Error: Container '$CONTAINER' not found."
    exit 1
fi

# Extract container details using docker inspect
IMAGE=$(docker inspect --format='{{.Config.Image}}' "$CONTAINER")
NAME=$(docker inspect --format='{{.Name}}' "$CONTAINER" | sed 's/^\/\(.*\)/\1/')
RESTART_POLICY=$(docker inspect --format='{{.HostConfig.RestartPolicy.Name}}' "$CONTAINER")
NETWORK_MODE=$(docker inspect --format='{{.HostConfig.NetworkMode}}' "$CONTAINER")
ENV_VARS=$(docker inspect --format='{{range .Config.Env}}-e '\''{{.}}'\'' ' "$CONTAINER")
PORTS=$(docker inspect --format='{{range $p, $conf := .HostConfig.PortBindings}}-p {{$p}}:{{(index $conf 0).HostPort}} {{$p}} {{end}}' "$CONTAINER")
VOLUMES=$(docker inspect --format='{{range .Mounts}}-v {{.Source}}:{{.Destination}} {{end}}' "$CONTAINER")
ENTRYPOINT=$(docker inspect --format='{{json .Config.Entrypoint}}' "$CONTAINER" | jq -r '. | join(" ")')
CMD=$(docker inspect --format='{{json .Config.Cmd}}' "$CONTAINER" | jq -r '. | join(" ")')
ENV_FILE=$(docker inspect --format='{{range .Config.EnvFile}}--env-file {{.}} {{end}}' "$CONTAINER" 2>/dev/null || echo "")
USER=$(docker inspect --format='{{.Config.User}}' "$CONTAINER")
HOSTNAME=$(docker inspect --format='{{.Config.Hostname}}' "$CONTAINER")
NETWORKS=$(docker inspect --format='{{range $key, $value := .NetworkSettings.Networks}}--network {{$key}} {{end}}' "$CONTAINER")
DEVICES=$(docker inspect --format='{{range .HostConfig.Devices}}--device {{.PathOnHost}}:{{.PathInContainer}} {{end}}' "$CONTAINER")
MEMORY=$(docker inspect --format='{{.HostConfig.Memory}}' "$CONTAINER")
CPUS=$(docker inspect --format='{{.HostConfig.NanoCpus}}' "$CONTAINER")
LOG_DRIVER=$(docker inspect --format='{{.HostConfig.LogConfig.Type}}' "$CONTAINER")
LOG_OPTIONS=$(docker inspect --format='{{range $key, $value := .HostConfig.LogConfig.Config}}--log-opt {{$key}}={{$value}} {{end}}' "$CONTAINER")
EXTRA_HOSTS=$(docker inspect --format='{{range .HostConfig.ExtraHosts}}--add-host {{.}} {{end}}' "$CONTAINER")
CAP_ADD=$(docker inspect --format='{{range .HostConfig.CapAdd}}--cap-add {{.}} {{end}}' "$CONTAINER")
CAP_DROP=$(docker inspect --format='{{range .HostConfig.CapDrop}}--cap-drop {{.}} {{end}}' "$CONTAINER")
DNS=$(docker inspect --format='{{range .HostConfig.Dns}}--dns {{.}} {{end}}' "$CONTAINER")
DNS_SEARCH=$(docker inspect --format='{{range .HostConfig.DnsSearch}}--dns-search {{.}} {{end}}' "$CONTAINER")

# Start building the docker run command
CMD_RUN="docker run -d"

# Add container name
CMD_RUN+=" --name $NAME"

# Add restart policy
if [ "$RESTART_POLICY" != "no" ]; then
    CMD_RUN+=" --restart $RESTART_POLICY"
fi

# Add port mappings
if [ -n "$PORTS" ]; then
    CMD_RUN+=" $PORTS"
fi

# Add volume mounts
if [ -n "$VOLUMES" ]; then
    CMD_RUN+=" $VOLUMES"
fi

# Add network settings
if [ "$NETWORK_MODE" != "bridge" ] && [ "$NETWORK_MODE" != "host" ] && [ "$NETWORK_MODE" != "none" ]; then
    CMD_RUN+=" --network $NETWORK_MODE"
fi

# Add environment variables
if [ -n "$ENV_VARS" ]; then
    CMD_RUN+=" $ENV_VARS"
fi

# Add environment file if exists
if [ -n "$ENV_FILE" ]; then
    CMD_RUN+=" $ENV_FILE"
fi

# Add user
if [ -n "$USER" ]; then
    CMD_RUN+=" --user $USER"
fi

# Add hostname
if [ -n "$HOSTNAME" ]; then
    CMD_RUN+=" --hostname $HOSTNAME"
fi

# Add devices
if [ -n "$DEVICES" ]; then
    CMD_RUN+=" $DEVICES"
fi

# Add memory limit
if [ "$MEMORY" -gt 0 ]; then
    MEM_MB=$((MEMORY / 1048576))
    CMD_RUN+=" --memory ${MEM_MB}m"
fi

# Add CPU limit
if [ "$CPUS" -gt 0 ]; then
    CPU_VALUE=$(echo "scale=2; $CPUS / 1000000000" | bc)
    CMD_RUN+=" --cpus $CPU_VALUE"
fi

# Add logging options
if [ -n "$LOG_DRIVER" ] && [ "$LOG_DRIVER" != "json-file" ]; then
    CMD_RUN+=" --log-driver $LOG_DRIVER"
fi

if [ -n "$LOG_OPTIONS" ]; then
    CMD_RUN+=" $LOG_OPTIONS"
fi

# Add extra hosts
if [ -n "$EXTRA_HOSTS" ]; then
    CMD_RUN+=" $EXTRA_HOSTS"
fi

# Add capabilities
if [ -n "$CAP_ADD" ]; then
    CMD_RUN+=" $CAP_ADD"
fi

if [ -n "$CAP_DROP" ]; then
    CMD_RUN+=" $CAP_DROP"
fi

# Add DNS settings
if [ -n "$DNS" ]; then
    CMD_RUN+=" $DNS"
fi

if [ -n "$DNS_SEARCH" ]; then
    CMD_RUN+=" $DNS_SEARCH"
fi

# Add entrypoint if it exists and is not empty
if [ "$ENTRYPOINT" != "null" ] && [ -n "$ENTRYPOINT" ]; then
    CMD_RUN+=" --entrypoint \"$ENTRYPOINT\""
fi

# Add image name
CMD_RUN+=" $IMAGE"

# Add command if it exists and is not empty
if [ "$CMD" != "null" ] && [ -n "$CMD" ]; then
    CMD_RUN+=" \"$CMD\""
fi

# Display the reconstructed docker run command
echo "Reconstructed 'docker run' command:"
echo
echo "$CMD_RUN"