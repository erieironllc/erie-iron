#!/bin/bash
set -e

exec > /var/log/user-data.log 2>&1

# no ECS!
rm -f /etc/ecs/ecs.config
rm -f /home/ec2-user/update_aws_cli.sh

echo "erie_ud begin updating .bashrc"
cat >> /home/ec2-user/.bashrc << 'EOF'
alias psw='bash /home/ec2-user/psw.sh'
alias pss='bash /home/ec2-user/pss.sh'
alias run_webservice_container='bash /home/ec2-user/run_webservice_container.sh'
alias run_shell='bash /home/ec2-user/run_shell.sh'
alias update_container='bash /home/ec2-user/update_container.sh'
alias nginx_access='sudo less /var/log/nginx/access.log'
alias nginx_error='sudo less /var/log/nginx/error.log'
alias ud_tail='tail -f /var/log/user-data.log'
alias ud_less='less /var/log/user-data.log'
alias ud_cat='cat /var/log/user-data.log'
alias ud_status='cat /var/log/user-data.log | grep erie_ud'
alias eb='vi ~/.bashrc && source ~/.bashrc'
function fe() {
    # Check if a search pattern was provided
    if [ -z "$1" ]; then
        echo "Usage: fe <search_pattern>"
        return 1
    fi

    # Use find to locate files that start with the given pattern
    # -type f ensures that only regular files are considered
    # -name "${1}*" matches files starting with the pattern
    local file
    file=$(find . -type f -name "${1}*" -print -quit)

    if [ -n "$file" ]; then
        # Open the first matched file with vi
        vi "$file"
    else
        echo "No files found matching pattern: $1"
    fi
}
EOF
source /home/ec2-user/.bashrc

echo "erie_ud begin updating keys"
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.repo | sudo tee /etc/yum.repos.d/nvidia-docker.repo
sudo rpm --import https://nvidia.github.io/nvidia-docker/gpgkey

cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'EOF'
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/nginx/access.log",
            "log_group_name": "nginx-access-logs",
            "log_stream_name": "{instance_id}-access",
            "timestamp_format": "%Y-%m-%d %H:%M:%S"
          },
          {
            "file_path": "/var/log/nginx/error.log",
            "log_group_name": "nginx-error-logs",
            "log_stream_name": "{instance_id}-error",
            "timestamp_format": "%Y-%m-%d %H:%M:%S"
          }
        ]
      }
    }
  },
  "metrics": {
    "namespace": "GPU",
    "metrics_collected": {
      "disk": {
        "measurement": ["used_percent", "free", "total"],
        "resources": ["/"],
        "ignore_file_system_types": ["sysfs", "devtmpfs"]
      },
      "nvidia_gpu": {
        "measurement": [
          {"name": "utilization_gpu", "unit": "Percent"},
          {"name": "utilization_memory", "unit": "Percent"},
          {"name": "memory_used", "unit": "Megabytes"},
          {"name": "memory_free", "unit": "Megabytes"},
          {"name": "memory_total", "unit": "Megabytes"}
        ],
        "metrics_collection_interval": 60
      }
    }
  }
}
EOF

cat > /home/ec2-user/update_container.sh << 'EOF'
#!/bin/bash

CONTAINER_NAME="erielab-messageprocessor-container"
CONTAINER_URI="471112823728.dkr.ecr.us-west-2.amazonaws.com/erielab-messageprocessor"

aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin $CONTAINER_URI

echo "Pulling latest image from ECR..."
docker pull $CONTAINER_URI:latest

if [ "$(docker ps -q -f name=$CONTAINER_NAME)" ]; then
    echo "Stopping existing container..."
    docker stop $CONTAINER_NAME
    docker rm $CONTAINER_NAME
fi

echo "Starting new container..."
docker run -d --name $CONTAINER_NAME --gpus all $CONTAINER_URI:latest
docker ps
EOF

cat > /home/ec2-user/run_shell.sh << 'EOF'
TAG=":latest"
if [ $1 ]; then
	TAG="@$1"
fi

echo $TAG

CONTAINER_URI="471112823728.dkr.ecr.us-west-2.amazonaws.com/erielab-messageprocessor$TAG"

aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin $CONTAINER_URI
echo "-> -> python manage.py run_ingest_benchmark --benchmark_type=xxlarge --max_threads=20 --count=2 --max_asset_duration_secs=600"
docker run -it --gpus all -e ERIELAB_ENV=prod_asset_processor $CONTAINER_URI /bin/bash
EOF


mkdir -p /tmp/erielab-container-volume

echo "erie_ud begin starting docker"
sudo service docker start
sudo usermod -a -G docker ec2-user
echo "erie_ud completed starting docker"

echo "erie_ud starting cloudwatch agent"
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config \
    -m ec2 \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \
    -s
sudo systemctl status amazon-cloudwatch-agent
echo "erie_ud completed starting cloudwatch agent"

cat <<EOF > /etc/docker/daemon.json
{
  "log-driver": "awslogs",
  "log-opts": {
    "awslogs-region": "us-west-2",
    "awslogs-group": "erielab-webservice-loggroup"
  }
}
EOF

cat > /home/ec2-user/run_message_processor.sh << 'EOF'
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin 471112823728.dkr.ecr.us-west-2.amazonaws.com/erielab-messageprocessor

docker stop erielab-messageprocessor-container || true
docker rm erielab-messageprocessor-container || true

docker rmi -f 471112823728.dkr.ecr.us-west-2.amazonaws.com/erielab-messageprocessor:latest || true
docker pull 471112823728.dkr.ecr.us-west-2.amazonaws.com/erielab-messageprocessor:latest

docker run \
  --name erielab-messageprocessor-container \
  --cpus 3.686 \
  --memory 15000m \
  --memory-reservation 11264m \
  --gpus all \
  -p 8001:8001 \
  -e ERIELAB_ENV=prod_asset_processor \
  -e AWS_REGION=us-west-2 \
  --log-driver awslogs \
  --log-opt awslogs-group=erielab-webservice-loggroup \
  --log-opt awslogs-region=us-west-2 \
  --log-opt mode=non-blocking \
  --log-opt max-buffer-size=25m \
  -v /opt/erielab-container-volume:/app/staticfiles \
  471112823728.dkr.ecr.us-west-2.amazonaws.com/erielab-messageprocessor:latest
EOF
chmod +x /home/ec2-user/run_message_processor.sh

cat <<EOF > /etc/systemd/system/erielab-messageprocessor-container.service
[Unit]
Description=Erie Message Processor Service
Requires=docker.service
After=docker.service

[Service]
Restart=always
RestartSec=5
KillMode=process
ExecStartPre=-/usr/bin/docker stop erielab-messageprocessor-container
ExecStartPre=-/usr/bin/docker rm erielab-messageprocessor-container
ExecStart=/home/ec2-user/run_message_processor.sh
ExecStop=/usr/bin/docker stop erielab-messageprocessor-container

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable erielab-messageprocessor-container

bash /home/ec2-user/run_message_processor.sh &

nvidia-smi
aws --version

echo "erie_ud ALL SET UP"