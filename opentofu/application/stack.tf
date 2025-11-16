terraform {
  required_version = ">= 1.6.0"

  backend "s3" {
    bucket         = "erieiron-opentofu-state"
    dynamodb_table = "opentofu-locks"
    encrypt        = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "StackIdentifier" {
  description = "Identifier used when naming application resources."
  type        = string
}

variable "DeletePolicy" {
  description = "Deletion policy hint (Retain/Delete)."
  type        = string
  default     = "Delete"
}

variable "ErieIronEnv" {
  description = "The env."
  type        = string
  default     = "dev"
}

variable "DomainName" {
  description = "Domain serving the public application endpoint."
  type        = string
}

variable "DomainHostedZoneId" {
  description = "Hosted zone ID associated with the application domain."
  type        = string
  default     = ""
}

variable "AlbCertificateArn" {
  description = "ACM certificate ARN for TLS termination."
  type        = string
}

variable "WebContainerImage" {
  description = "Container image for the Django web service."
  type        = string
}

variable "WebContainerCpu" {
  description = "Fargate task CPU units."
  type        = number
  default     = 512
}

variable "WebContainerMemory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 1024
}

variable "WebDesiredCount" {
  description = "Desired number of running tasks."
  type        = number
  default     = 1
  validation {
    condition     = var.WebDesiredCount >= 1
    error_message = "WebDesiredCount must be at least 1."
  }
}

variable "StaticCompiledDir" {
  description = "Directory containing compiled static assets within the container."
  type        = string
  default     = "erieiron_ui/static/compiled"
}

variable "DatabaseName" {
  description = "Database name exposed to the application container."
  type        = string
  default     = "appdb"
}

variable "FoundationStackIdentifier" {
  description = "Identifier used to name foundation resources."
  type        = string
  default     = null
}

variable "ClientIpForRemoteAccess" {
  description = "Developer IPv4 address in /32 notation allowed to reach the database."
  type        = string
  default     = "0.0.0.0/32"
}

variable "VpcId" {
  description = "VPC identifier hosting the workload."
  type        = string
}

variable "VpcCidr" {
  description = "CIDR block of the shared VPC."
  type        = string
}

variable "PublicSubnet1Id" {
  description = "Public subnet ID used by the load balancer and database subnet group."
  type        = string
}

variable "PublicSubnet2Id" {
  description = "Second public subnet ID used by the load balancer and database subnet group."
  type        = string
}

variable "PrivateSubnet1Id" {
  description = "Private subnet ID for ECS tasks."
  type        = string
}

variable "PrivateSubnet2Id" {
  description = "Second private subnet ID for ECS tasks."
  type        = string
}

variable "SecurityGroupId" {
  description = "Shared security group applied to exposed components."
  type        = string
}

variable "DBInstanceClass" {
  description = "RDS instance class."
  type        = string
  default     = "db.t3.micro"
}

variable "DBAllocatedStorage" {
  description = "Allocated storage in GiB for the database instance."
  type        = number
  default     = 20
}

variable "AdminPassword" {
  description = "Optional generated admin password forwarded by orchestration logic."
  type        = string
  default     = null
}

variable "AWS_ACCOUNT_ID" {
  description = "AWS account identifier (present for compatibility)."
  type        = string
  default     = null
}

variable "tags" {
  description = "Additional tags to apply to managed resources."
  type        = map(string)
  default     = {}
}
locals {
  retain_resources     = lower(coalesce(var.DeletePolicy, "delete")) == "retain"
  hosted_zone_provided = length(trim(var.DomainHostedZoneId, " ")) > 0
  database_name        = "appdb"
  foundation_identifier = coalesce(var.FoundationStackIdentifier, var.StackIdentifier)

  base_tags = merge(
    {
      Name            = "${var.StackIdentifier}"
      StackIdentifier = var.StackIdentifier
    },
    var.tags
  )
  
  foundation_tags = merge(
    {
      Name            = "${local.foundation_identifier}"
      StackIdentifier = local.foundation_identifier
    },
    var.tags
  )
}
data "aws_region" "current" {}

data "aws_partition" "current" {}

data "aws_caller_identity" "current" {}

resource "aws_lb" "web" {
  name               = substr("${var.StackIdentifier}-alb", 0, 32)
  load_balancer_type = "application"
  internal           = false
  security_groups    = [var.SecurityGroupId]
  subnets            = [var.PublicSubnet1Id, var.PublicSubnet2Id]

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-alb"
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_lb_target_group" "web" {
  name        = substr("${var.StackIdentifier}-tg", 0, 32)
  port        = 8006
  protocol    = "HTTP"
  vpc_id      = var.VpcId
  target_type = "ip"

  health_check {
    enabled             = true
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
    matcher             = "200-399"
    path                = "/health/"
    protocol            = "HTTP"
  }

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-tg"
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.web.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.web.arn
  port              = 443
  protocol          = "HTTPS"

  certificate_arn = var.AlbCertificateArn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_ecs_cluster" "web" {
  name = "${var.StackIdentifier}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-cluster"
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_cloudwatch_log_group" "web" {
  name              = "/ecs/${var.StackIdentifier}-web"
  retention_in_days = 30

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_iam_role" "web_execution" {
  name = substr("${var.StackIdentifier}-task-execution-role", 0, 64)

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-task-execution-role"
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}


resource "aws_iam_role_policy_attachment" "web_execution" {
  role       = aws_iam_role.web_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "web_execution_assume_target" {
  name = "${var.StackIdentifier}-assume-target-role"
  role = aws_iam_role.web_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Resource = "arn:aws:iam::*:role/ErieIronTargetAccountAgentRole"
      }
    ]
  })
}

resource "aws_iam_role" "web_task" {
  name = substr("${var.StackIdentifier}-task-role", 0, 64)

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-task-role"
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_iam_role_policy" "web_secrets" {
  name = "${var.StackIdentifier}-web-secrets"
  role = aws_iam_role.web_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowReadRdsSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_db_instance.primary.master_user_secret[0].secret_arn
      }
    ]
  })

  depends_on = [aws_db_instance.primary]
}

resource "aws_iam_role_policy" "web_llm_api_keys" {
  name = "${var.StackIdentifier}-web-llm-api-keys"
  role = aws_iam_role.web_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowReadLlmApiKeys"
        Effect   = "Allow"
        Action   = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = "arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:LLM_API_KEYS*"
      }
    ]
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_ecs_task_definition" "web" {
  family                   = substr("${var.StackIdentifier}-web", 0, 255)
  cpu                      = tostring(var.WebContainerCpu)
  memory                   = tostring(var.WebContainerMemory)
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  execution_role_arn = aws_iam_role.web_execution.arn
  task_role_arn      = aws_iam_role.web_task.arn

  container_definitions = jsonencode([
    {
      name      = "web"
      essential = true
      image     = var.WebContainerImage
      portMappings = [
        {
          containerPort = 8006
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "ALLOWED_HOSTS", value = "*" },
        { name = "RDS_SECRET_ARN", value = aws_db_instance.primary.master_user_secret[0].secret_arn },
        { name = "ERIEIRON_ENV", value = var.ErieIronEnv },
        { name = "ERIEIRON_DB_NAME", value = local.database_name },
        { name = "ERIEIRON_DB_HOST", value = aws_db_instance.primary.address },
        { name = "ERIEIRON_DB_PORT", value = tostring(aws_db_instance.primary.port) },
        { name = "STATIC_COMPILED_DIR", value = var.StaticCompiledDir },
        { name = "AWS_DEFAULT_REGION", value = data.aws_region.current.name },
        { name = "DOMAIN_NAME", value = var.DomainName }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.web.name
          awslogs-region        = data.aws_region.current.name
          awslogs-stream-prefix = "web"
        }
      }
    }
  ])

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-task"
  })
}

resource "aws_ecs_service" "web" {
  name            = "${var.StackIdentifier}-service"
  cluster         = aws_ecs_cluster.web.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.WebDesiredCount
  launch_type     = "FARGATE"

  health_check_grace_period_seconds = 60

  deployment_controller {
    type = "ECS"
  }

  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 100
  enable_ecs_managed_tags            = true
  propagate_tags                     = "SERVICE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = [var.PrivateSubnet1Id, var.PrivateSubnet2Id]
    security_groups  = [var.SecurityGroupId]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8006
  }

  depends_on = [aws_lb_listener.https]

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-service"
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

# Database Resources (formerly from foundation stack)
resource "aws_db_subnet_group" "primary" {
  name       = "${local.foundation_identifier}-db-subnet-group"
  subnet_ids = [var.PublicSubnet1Id, var.PublicSubnet2Id]

  tags = merge(local.foundation_tags, {
    Name = "${local.foundation_identifier}-db-subnet-group"
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_db_instance" "primary" {
  identifier                  = "${local.foundation_identifier}-db"
  engine                      = "postgres"
  db_name                     = local.database_name
  instance_class              = var.DBInstanceClass
  allocated_storage           = var.DBAllocatedStorage
  storage_type                = "gp3"
  multi_az                    = false
  publicly_accessible         = true
  storage_encrypted           = true
  backup_retention_period     = 7
  manage_master_user_password = true
  username                    = "postgres"
  db_subnet_group_name        = aws_db_subnet_group.primary.name
  vpc_security_group_ids      = [var.SecurityGroupId]
  skip_final_snapshot         = true

  tags = merge(local.foundation_tags, {
    Name = "${local.foundation_identifier}-db-instance"
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "null_resource" "enable_pgvector" {
  depends_on = [aws_db_instance.primary]

  provisioner "local-exec" {
    command = <<EOT
set -e
echo "Fetching DB credentials from Secrets Manager..."

SECRET_JSON=$(aws secretsmanager get-secret-value \
  --secret-id ${aws_db_instance.primary.master_user_secret[0].secret_arn} \
  --query SecretString --output text)

PGPASSWORD=$(echo "$SECRET_JSON" | jq -r '.password')
PGUSER=$(echo "$SECRET_JSON" | jq -r '.username')

echo "Waiting for DB to accept connections..."
for i in {1..30}; do
  pg_isready -h ${aws_db_instance.primary.address} -U "$PGUSER" -d ${local.database_name} && break
  echo "Still waiting..."
  sleep 10
done

echo "Installing pgvector extension..."
PGPASSWORD="$PGPASSWORD" psql "host=${aws_db_instance.primary.address} user=$PGUSER dbname=${local.database_name}" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo "pgvector installation complete."
EOT
  }

  triggers = {
    db_instance_id = aws_db_instance.primary.id
  }
}

resource "aws_security_group_rule" "rds_ingress_vpc" {
  security_group_id = var.SecurityGroupId
  type              = "ingress"
  from_port         = 5432
  to_port           = 5432
  protocol          = "tcp"
  cidr_blocks       = [var.VpcCidr]
  description       = "Allow Postgres from shared VPC CIDR for internal access"
}

resource "aws_security_group_rule" "rds_ingress_client" {
  security_group_id = var.SecurityGroupId
  type              = "ingress"
  from_port         = 5432
  to_port           = 5432
  protocol          = "tcp"
  cidr_blocks       = [var.ClientIpForRemoteAccess]
  description       = "Allow developer IP to reach Postgres for migrations"
}

variable "CreateIngressRule" {
  description = "Whether to create the ECS ingress rule (useful when security group is shared)."
  type        = bool
  default     = true
}

resource "aws_security_group_rule" "ecs_ingress" {
  count             = var.CreateIngressRule ? 1 : 0
  security_group_id = var.SecurityGroupId
  type              = "ingress"
  from_port         = 8006
  to_port           = 8006
  protocol          = "tcp"
  cidr_blocks       = [var.VpcCidr]
  description       = "Allow ALB to reach ECS tasks on port 8006"
}
output "LoadBalancerDNSName" {
  description = "Public DNS name of the application load balancer."
  value       = aws_lb.web.dns_name
}

output "ApplicationUrl" {
  description = "HTTPS endpoint for the deployed Django application."
  value       = format("https://%s", var.DomainName)
}

output "EcsClusterName" {
  description = "ECS cluster name hosting the web service."
  value       = aws_ecs_cluster.web.name
}

output "TargetGroupArn" {
  description = "Target group ARN used by the web service."
  value       = aws_lb_target_group.web.arn
}

# Database outputs (formerly from foundation stack)
output "FoundationStackIdentifier" {
  description = "Initiative-level identifier for persistent resources."
  value       = local.foundation_identifier
}

output "RdsInstanceIdentifier" {
  description = "Identifier of the provisioned RDS instance."
  value       = aws_db_instance.primary.id
}

output "RdsInstanceEndpoint" {
  description = "Endpoint address of the RDS instance."
  value       = aws_db_instance.primary.address
}

output "RdsEndpointAddress" {
  description = "Hostname of the database endpoint."
  value       = aws_db_instance.primary.address
}

output "RdsInstancePort" {
  description = "Endpoint port of the RDS instance."
  value       = aws_db_instance.primary.port
}

output "RdsEndpointPort" {
  description = "Port of the database endpoint."
  value       = tostring(aws_db_instance.primary.port)
}

output "RdsInstanceDBName" {
  description = "Database name configured on the RDS instance."
  value       = local.database_name
}

output "RdsMasterSecretArn" {
  description = "ARN of the generated Secrets Manager secret for the DB master user."
  value       = try(aws_db_instance.primary.master_user_secret[0].secret_arn, null)
  sensitive   = true
}

output "RdsSecretArn" {
  description = "Alias of the generated Secrets Manager secret for the DB master user."
  value       = try(aws_db_instance.primary.master_user_secret[0].secret_arn, null)
  sensitive   = true
}
