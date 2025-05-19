#!/bin/bash
exec > >(logger -t baremetal-container-manager) 2>&1

echo "running weekly maintenance"
docker system prune -f

aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin 471112823728.dkr.ecr.us-west-2.amazonaws.com
docker pull 471112823728.dkr.ecr.us-west-2.amazonaws.com/erielab-messageprocessor:latest
