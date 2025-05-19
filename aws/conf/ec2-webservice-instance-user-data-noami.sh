#!/bin/bash
set -e
exec > /var/log/user-data.log 2>&1

echo "erie_ud begin starting ecs"
echo ECS_CLUSTER=erie-webservice-ecs-cluster > /etc/ecs/ecs.config
echo "erie_ud completed starting ecs"

echo "erie_ud begin updating .bashrc"
cat >> /home/ec2-user/.bashrc << 'EOF'
alias psw='bash /home/ec2-user/psw.sh'
alias pss='bash /home/ec2-user/pss.sh'
alias run_webservice_container='bash /home/ec2-user/run_webservice_container.sh'
alias run_shell='bash /home/ec2-user/run_shell.sh'
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
echo "erie_ud complete updating .bashrc"

# echo "erie_ud begin yum update"
# sudo yum update -y
# echo "erie_ud complete yum update"

echo "erie_ud begin amazon-linux-extras"
sudo amazon-linux-extras enable nginx1
echo "erie_ud complete amazon-linux-extras"

echo "erie_ud install docker"
sudo yum install -y docker
echo "erie_ud install git"
sudo yum install -y git
echo "erie_ud install nginx"
sudo yum install -y nginx
echo "erie_ud install wget"
sudo yum install -y wget
echo "erie_ud install dkms"
sudo yum install -y dkms
echo "erie_ud install awscli"
sudo yum install -y awscli
echo "erie_ud install unzip"
sudo yum install -y unzip
echo "erie_ud install yum-utils"
sudo yum install -y yum-utils
echo "erie_ud install device-mapper-persistent-data"
sudo yum install -y device-mapper-persistent-data
echo "erie_ud install lvm2"
sudo yum install -y lvm2
echo "erie_ud install openssl11"
sudo yum install -y openssl11
echo "erie_ud install amazon-cloudwatch-agent"
sudo yum install -y amazon-cloudwatch-agent
echo "erie_ud completeupdating packages"


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
    "namespace": "EC2",
    "metrics_collected": {
      "disk": {
        "measurement": ["used_percent", "free", "total"],
        "resources": ["/"],
        "ignore_file_system_types": ["sysfs", "devtmpfs"]
      }
    }
  }
}
EOF


cat > /home/ec2-user/psw.sh << 'EOF'
watch docker ps && docker logs -f $(docker ps -a --latest --quiet) | grep -v "health_check" | grep -v "08117310-60e1-70b4-2ef8-b6f4dfb511b8" | grep -v "ANON"
EOF

cat > /home/ec2-user/run_webservice_container.sh << 'EOF'
TAG=":latest"
if [ $1 ]; then
	TAG="@$1"
fi

echo $TAG

CONTAINER_URI="471112823728.dkr.ecr.us-west-2.amazonaws.com/erielab-webservice$TAG"

aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin $CONTAINER_URI
docker run -it $CONTAINER_URI
EOF

cat > /home/ec2-user/run_shell.sh << 'EOF'
TAG=":latest"
if [ $1 ]; then
	TAG="@$1"
fi

echo $TAG

CONTAINER_URI="471112823728.dkr.ecr.us-west-2.amazonaws.com/erielab-webservice$TAG"

aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin $CONTAINER_URI
echo "-> -> python manage.py run_ingest_benchmark --benchmark_type=xxlarge --max_threads=20 --count=2 --max_asset_duration_secs=600"
docker run -it $CONTAINER_URI /bin/bash
EOF

cat > /home/ec2-user/pss.sh << 'EOF'
watch docker ps
docker exec -it $(docker ps -a --latest --quiet) /bin/bash
EOF

mkdir -p /tmp/erielab-container-volume

cat > /etc/nginx/conf.d/default.conf << EOF
server {
    client_max_body_size 100M;

    listen 80;
    server_name localhost;

    location / {
        proxy_pass http://127.0.0.1:8001;

        proxy_read_timeout 600s;
        proxy_connect_timeout 600s;
        proxy_send_timeout 600s;

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Port \$server_port;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location /static/ {
      alias /opt/erielab-container-volume/;
    }
}
EOF

echo "erie_ud begin starting docker"
sudo service docker start
sudo usermod -a -G docker ec2-user
echo "erie_ud completed starting docker"

echo "erie_ud begin starting ngnix"
sudo systemctl enable nginx
sudo systemctl start nginx
echo "erie_ud completed starting ngnix"

echo "erie_ud starting cloudwatch agent"
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config \
    -m ec2 \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json \
    -s
sudo systemctl status amazon-cloudwatch-agent
echo "erie_ud completed starting cloudwatch agent"


echo "erie_ud begin nginx restart"
systemctl restart nginx
echo "erie_ud complete nginx restart"

aws --version
docker ps
source /home/ec2-user/.bashrc
cat /etc/ecs/ecs.config
echo "erie_ud ALL SET UP"