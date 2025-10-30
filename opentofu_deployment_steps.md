# Erie Iron OpenTofu Deployment Guide

This guide provides step-by-step instructions for deploying Erie Iron to AWS using OpenTofu (Terraform). The deployment follows a two-tier architecture pattern with foundation and application stacks.

## Overview

Erie Iron uses a **two-tier deployment model**:
1. **Foundation Stack**: Core infrastructure (VPC, RDS, ECS cluster, ECR repositories)
2. **Application Stack**: Application services (ECS services, load balancer, domain configuration)

### Automation Integration

- The autonomous agent selects the IaC backend via the `SELF_DRIVING_IAC_PROVIDER` setting (default `opentofu`). Set this environment variable to `cloudformation` to run the legacy provider.
- `InfrastructureStack.stack_arn` now stores a serialized state descriptor (workspace name, path, and state file) so UI and downstream systems can locate OpenTofu workspaces without AWS-specific identifiers.
- The business and initiative infrastructure tabs now surface provider-neutral "IaC Logs" that render OpenTofu command output, plan summaries, and captured exceptions alongside legacy CloudFormation fallbacks.
- Follow-up cleanups: migrate the `SelfDrivingTaskIteration.cloudformation_logs` column to `iac_logs` once all consumers are switched, and schedule retirement of unused CloudFormation helpers after OpenTofu reaches full parity.

## Prerequisites

### 1. Install Required Tools

**OpenTofu Installation:**
```bash
# macOS (using Homebrew)
brew tap opentofu/tap
brew install opentofu

# Linux (using curl)
curl -s https://get.opentofu.org/install-opentofu.sh | bash

# Verify installation
tofu version
```

**AWS CLI:**
```bash
# Install AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Configure AWS credentials
aws configure
```

### 2. AWS Account Setup

**Required AWS Permissions:**
- VPC management (EC2 full access)
- RDS management
- ECS full access
- ECR full access
- IAM role and policy management
- Secrets Manager access
- CloudWatch Logs access
- Route 53 access (if using custom domain)
- S3 management

**Verify AWS Access:**
```bash
aws sts get-caller-identity
aws ec2 describe-regions
```

### 3. Docker Setup

Ensure Docker is installed and running for container image builds:
```bash
docker --version
docker info
```

## Deployment Steps

### Step 1: Prepare the Deployment Environment

**1.1 Clone and Navigate to Project:**
```bash
cd /path/to/erieiron
cd opentofu/
```

**1.2 Initialize OpenTofu Backend (Optional but Recommended):**

Create a `backend.tf` file for remote state storage:
```hcl
# backend.tf
terraform {
  backend "s3" {
    bucket = "your-terraform-state-bucket"
    key    = "erieiron/foundation/terraform.tfstate"
    region = "us-west-2"
  }
}
```

### Step 2: Deploy Foundation Stack

**2.1 Initialize Foundation Configuration:**
```bash
# Navigate to foundation configuration
cd foundation/
# Or use the single foundation.tf file
tofu init
```

**2.2 Plan Foundation Deployment:**
```bash
tofu plan -var="environment_name=dev" \
         -var="project_name=ErieIron" \
         -var="db_instance_class=db.t4g.micro"
```

**2.3 Deploy Foundation Stack:**
```bash
tofu apply -var="environment_name=dev" \
          -var="project_name=ErieIron" \
          -var="db_instance_class=db.t4g.micro"
```

**2.4 Capture Foundation Outputs:**
```bash
# Save outputs for application stack
tofu output -json > foundation-outputs.json

# Key outputs needed:
# - vpc_id
# - subnet_id_list
# - cluster_name
# - ecr_webservice_uri
# - ecr_messageprocessor_uri
# - database_endpoint
# - database_secret_arn
```

### Step 3: Build and Push Container Images

**3.1 Authenticate with ECR:**
```bash
# Get ECR login token
aws ecr get-login-password --region us-west-2 | \
docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-west-2.amazonaws.com
```

**3.2 Build Web Service Container:**
```bash
# From project root directory
docker build -f Dockerfile.webservice \
  --build-arg ERIEIRON_ENV=dev \
  -t erieiron-webservice:latest .

# Tag for ECR
docker tag erieiron-webservice:latest \
  <ecr-webservice-uri>:latest

# Push to ECR
docker push <ecr-webservice-uri>:latest
```

**3.3 Build Message Processor Container:**
```bash
# Build message processor
docker build -f Dockerfile.messageprocessor \
  --build-arg ERIEIRON_ENV=dev \
  -t erieiron-messageprocessor:latest .

# Tag for ECR
docker tag erieiron-messageprocessor:latest \
  <ecr-messageprocessor-uri>:latest

# Push to ECR
docker push <ecr-messageprocessor-uri>:latest
```

### Step 4: Deploy Application Stack

**4.1 Initialize Application Configuration:**
```bash
# Navigate to application configuration
cd ../application/
# Or prepare application.tf variables
tofu init
```

**4.2 Prepare Application Variables:**

Create a `terraform.tfvars` file:
```hcl
environment_name = "dev"
project_name     = "ErieIron"
domain_name      = "your-domain.com"  # Optional

# Foundation stack outputs
vpc_id                     = "vpc-xxxxxxxxx"
subnet_ids                 = ["subnet-xxxxxxxxx", "subnet-yyyyyyyyy"]
cluster_name              = "AppCluster-dev"
ecr_webservice_uri        = "123456789012.dkr.ecr.us-west-2.amazonaws.com/erieiron-webservice-dev"
ecr_messageprocessor_uri  = "123456789012.dkr.ecr.us-west-2.amazonaws.com/erieiron-messageprocessor-dev"
database_endpoint         = "erieiron-db-dev.xxxxxxxxx.us-west-2.rds.amazonaws.com"
database_secret_arn       = "arn:aws:secretsmanager:us-west-2:123456789012:secret:ErieIron-rds-secret-dev-xxxxxx"
```

**4.3 Plan Application Deployment:**
```bash
tofu plan -var-file="terraform.tfvars"
```

**4.4 Deploy Application Stack:**
```bash
tofu apply -var-file="terraform.tfvars"
```

### Step 5: Verify Deployment

**5.1 Check ECS Services:**
```bash
aws ecs describe-services \
  --cluster AppCluster-dev \
  --services ErieIron-webservice-dev ErieIron-messageprocessor-dev
```

**5.2 Check Load Balancer:**
```bash
aws elbv2 describe-load-balancers \
  --names erieiron-alb-dev
```

**5.3 Test Application:**
```bash
# Get load balancer DNS name
LB_DNS=$(tofu output -raw load_balancer_dns)
echo "Application URL: http://$LB_DNS"

# Test health endpoint
curl -f http://$LB_DNS/health/
```

### Step 6: Database Migration

**6.1 Connect to Database:**
```bash
# Get database credentials from Secrets Manager
aws secretsmanager get-secret-value \
  --secret-id ErieIron-rds-secret-dev \
  --query SecretString --output text

# Connect using psql or your preferred client
psql -h <database-endpoint> -p 5432 -U masteruser -d erieiron
```

**6.2 Run Django Migrations:**
```bash
# From within the web service container or locally
python manage.py migrate
python manage.py collectstatic --noinput
```

## Environment-Specific Deployments

### Development Environment
```bash
# Use default development settings
tofu apply -var="environment_name=dev"
```

### Staging Environment
```bash
# Deploy to staging with higher resources
tofu apply -var="environment_name=stage" \
          -var="db_instance_class=db.t4g.small"
```

### Production Environment
```bash
# Deploy to production with production-grade resources
tofu apply -var="environment_name=prod" \
          -var="db_instance_class=db.t4g.medium" \
          -var="domain_name=yourdomain.com"
```

## Advanced Configuration

### Custom Domain Setup

**1. Configure Domain in Application Stack:**
```hcl
# In terraform.tfvars
domain_name = "erieiron.yourdomain.com"
```

**2. Update DNS:**
```bash
# Get Route 53 name servers
aws route53 get-hosted-zone --id <hosted-zone-id>

# Update your domain registrar to use Route 53 name servers
```

### SSL Certificate Setup

Add SSL support by integrating with AWS Certificate Manager:
```hcl
# Add to application.tf
resource "aws_acm_certificate" "main" {
  domain_name       = var.domain_name
  validation_method = "DNS"
  
  lifecycle {
    create_before_destroy = true
  }
}

# Update ALB listener for HTTPS
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = "443"
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS-1-2-2017-01"
  certificate_arn   = aws_acm_certificate.main.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.webservice.arn
  }
}
```

## Troubleshooting

### Common Issues

**1. ECS Service Startup Issues:**
```bash
# Check service events
aws ecs describe-services --cluster <cluster-name> --services <service-name>

# Check task logs
aws logs tail <log-group-name> --follow
```

**2. Database Connection Issues:**
```bash
# Verify security group rules
aws ec2 describe-security-groups --group-ids <sg-id>

# Test database connectivity
telnet <db-endpoint> 5432
```

**3. Container Image Issues:**
```bash
# Verify ECR images
aws ecr describe-images --repository-name <repo-name>

# Check ECS task definition
aws ecs describe-task-definition --task-definition <task-def-name>
```

### Cleanup

**Remove Application Stack:**
```bash
cd application/
tofu destroy -var-file="terraform.tfvars"
```

**Remove Foundation Stack:**
```bash
cd ../foundation/
tofu destroy -var="environment_name=dev"
```

## Monitoring and Maintenance

### CloudWatch Monitoring
- ECS service metrics are automatically available in CloudWatch
- Application logs are streamed to CloudWatch Logs
- Set up CloudWatch alarms for key metrics

### Updates and Scaling
```bash
# Update ECS service with new image
aws ecs update-service \
  --cluster <cluster-name> \
  --service <service-name> \
  --force-new-deployment

# Scale ECS service
aws ecs update-service \
  --cluster <cluster-name> \
  --service <service-name> \
  --desired-count 2
```

## Security Considerations

1. **Use IAM roles** instead of access keys where possible
2. **Enable VPC Flow Logs** for network monitoring
3. **Use AWS Secrets Manager** for all sensitive configuration
4. **Enable CloudTrail** for audit logging
5. **Regular security updates** for container images
6. **Network segmentation** with proper security groups

## Cost Optimization

1. **Use appropriate instance sizes** for your workload
2. **Enable RDS storage autoscaling** 
3. **Consider Reserved Instances** for production
4. **Set up billing alerts** in AWS
5. **Use Fargate Spot** for development environments

For additional support, refer to the Erie Iron documentation or contact the development team.
