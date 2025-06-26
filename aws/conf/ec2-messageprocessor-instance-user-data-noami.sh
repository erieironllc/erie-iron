#!/bin/bash
set -e

exec > /var/log/user-data.log 2>&1

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

echo "erie_ud begin updating keys"
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.repo | sudo tee /etc/yum.repos.d/nvidia-docker.repo
sudo rpm --import https://nvidia.github.io/nvidia-docker/gpgkey


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

cat > /home/ec2-user/psw.sh << 'EOF'
watch docker ps && docker logs -f $(docker ps -a --latest --quiet) | grep -v "health_check" | grep -v "08117310-60e1-70b4-2ef8-b6f4dfb511b8" | grep -v "ANON"
EOF

cat > /home/ec2-user/run_webservice_container.sh << 'EOF'
TAG=":latest"
if [ $1 ]; then
	TAG="@$1"
fi

echo $TAG

CONTAINER_URI="782005355493.dkr.ecr.us-west-2.amazonaws.com/erielab-messageprocessor$TAG"

aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin $CONTAINER_URI
docker run -it --gpus all $CONTAINER_URI
EOF

cat > /home/ec2-user/run_shell.sh << 'EOF'
TAG=":latest"
if [ $1 ]; then
	TAG="@$1"
fi

echo $TAG

CONTAINER_URI="782005355493.dkr.ecr.us-west-2.amazonaws.com/erielab-messageprocessor$TAG"

aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin $CONTAINER_URI
echo "-> -> python manage.py run_ingest_benchmark --benchmark_type=xxlarge --max_threads=20 --count=2 --max_asset_duration_secs=600"
docker run -it --gpus all -e ERIELAB_ENV=prod_asset_processor $CONTAINER_URI /bin/bash
EOF

cat > /home/ec2-user/pss.sh << 'EOF'
watch docker ps
docker exec -it $(docker ps -a --latest --quiet) /bin/bash
EOF

cat > /home/ec2-user/update_aws_cli.sh << 'EOF'
    # Exit immediately if a command exits with a non-zero status
    set -e

    # Function to display messages
    function echo_info() {
        echo -e "\e[34m[INFO]\e[0m $1"
    }

    function echo_success() {
        echo -e "\e[32m[SUCCESS]\e[0m $1"
    }

    function echo_warning() {
        echo -e "\e[33m[WARNING]\e[0m $1"
    }

    function echo_error() {
        echo -e "\e[31m[ERROR]\e[0m $1"
    }

    # Ensure the script is run as root or with sudo
    if [[ "$EUID" -ne 0 ]]; then
        echo_error "Please run this script with sudo or as root."
        exit 1
    fi

    # 1. Uninstall AWS CLI v1 if installed via yum
    echo_info "Checking if AWS CLI v1 is installed via yum..."
    if rpm -qa | grep -qw awscli; then
        echo_info "AWS CLI v1 found via yum. Removing..."
        yum remove awscli -y
        echo_success "AWS CLI v1 removed via yum."
    else
        echo_info "AWS CLI v1 not found via yum."
    fi

    # 2. Uninstall AWS CLI v1 if installed via pip
    echo_info "Checking if AWS CLI v1 is installed via pip..."
    if command -v pip &> /dev/null; then
        if pip show awscli &> /dev/null; then
            echo_info "AWS CLI v1 found via pip. Removing..."
            pip uninstall awscli -y
            echo_success "AWS CLI v1 removed via pip."
        else
            echo_info "AWS CLI v1 not found via pip."
        fi
    else
        echo_info "pip not found. Skipping pip uninstallation."
    fi

    # 3. Download the latest AWS CLI v2 installer
    TEMP_DIR=$(mktemp -d)
    echo_info "Downloading AWS CLI v2 installer..."
    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "$TEMP_DIR/awscliv2.zip"

    echo_info "Extracting AWS CLI v2 installer..."
    unzip -q "$TEMP_DIR/awscliv2.zip" -d "$TEMP_DIR"

    # 4. Install or update AWS CLI v2
    echo_info "Installing/Updating AWS CLI v2..."
    "$TEMP_DIR/aws/install" --update

    echo_success "AWS CLI v2 installation/update completed."

    # 5. Add AWS CLI v2 to the system PATH
    AWS_V2_PATH="/usr/local/aws-cli/v2/current/bin"
    PROFILE_D_FILE="/etc/profile.d/awscli.sh"

    if [ ! -f "$PROFILE_D_FILE" ]; then
        echo_info "Creating profile script to add AWS CLI v2 to PATH..."
        echo "export PATH=$AWS_V2_PATH:\$PATH" > "$PROFILE_D_FILE"
        chmod +x "$PROFILE_D_FILE"
        echo_success "Profile script created at $PROFILE_D_FILE."
    else
        echo_info "Profile script already exists at $PROFILE_D_FILE."
    fi

    # Source the profile script to update PATH for the current session
    export PATH="$AWS_V2_PATH:$PATH"

    # 6. Create a symbolic link to ensure 'aws' points to AWS CLI v2
    SYMLINK_PATH="/usr/local/bin/aws"

    if [ -L "$SYMLINK_PATH" ] || [ -f "$SYMLINK_PATH" ]; then
        echo_info "Updating symbolic link for 'aws' to point to AWS CLI v2..."
        ln -sf "$AWS_V2_PATH/aws" "$SYMLINK_PATH"
        echo_success "Symbolic link updated at $SYMLINK_PATH."
    else
        echo_info "Creating symbolic link for 'aws' to AWS CLI v2..."
        ln -s "$AWS_V2_PATH/aws" "$SYMLINK_PATH"
        echo_success "Symbolic link created at $SYMLINK_PATH."
    fi

    # 7. Clean up temporary files
    echo_info "Cleaning up temporary files..."
    rm -rf "$TEMP_DIR"
    echo_success "Temporary files removed."

    # 8. Verify AWS CLI installation
    echo_info "Verifying AWS CLI installation..."
    AWS_VERSION=$(aws --version)

    if [[ $AWS_VERSION == aws-cli/2* ]]; then
        echo_success "AWS CLI v2 is installed successfully."
        echo "Version: $AWS_VERSION"
    else
        echo_error "AWS CLI v2 installation failed or AWS CLI v1 is still in use."
        echo "Current AWS version: $AWS_VERSION"
        exit 1
    fi

    echo_info "AWS CLI v2 installation and configuration completed successfully."
EOF

mkdir -p /tmp/erielab-container-volume

echo "erie_ud begin starting docker"
sudo service docker start
sudo usermod -a -G docker ec2-user
echo "erie_ud completed starting docker"

echo "erie_ud begin starting ngnix"
sudo systemctl enable nginx
sudo systemctl start nginx
echo "erie_ud completed starting ngnix"

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
echo "erie_ud begin updating aws cli"
sudo bash /home/ec2-user/update_aws_cli.sh
echo "erie_ud complete updating aws cli"

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


echo "erie_ud begin starting ecs"
echo ECS_CLUSTER=erielab-messageprocessor-ecs-cluster > /etc/ecs/ecs.config
echo "erie_ud completed starting ecs"

dd if=/dev/zero of=/swapfile bs=1G count=10
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile swap swap defaults 0 0' >> /etc/fstab
swapon --show

nvidia-smi
aws --version
docker ps
echo "erie_ud ALL SET UP"