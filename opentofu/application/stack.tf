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

variable "VpcId" {
  description = "VPC identifier hosting the workload."
  type        = string
}

variable "VpcCidr" {
  description = "CIDR block of the shared VPC."
  type        = string
}

variable "PublicSubnet1Id" {
  description = "Public subnet ID used by the load balancer."
  type        = string
}

variable "PublicSubnet2Id" {
  description = "Second public subnet ID used by the load balancer."
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

variable "RdsSecretArn" {
  description = "Secrets Manager ARN that stores database credentials."
  type        = string
}

variable "RdsEndpointAddress" {
  description = "Hostname of the database endpoint."
  type        = string
}

variable "RdsEndpointPort" {
  description = "Port of the database endpoint."
  type        = string
}

variable "tags" {
  description = "Additional tags to apply to managed resources."
  type        = map(string)
  default     = {}
}
locals {
  base_tags = merge(
    {
      Name            = "${var.StackIdentifier}"
      StackIdentifier = var.StackIdentifier
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
        Resource = var.RdsSecretArn
      }
    ]
  })
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
        { name = "RDS_SECRET_ARN", value = var.RdsSecretArn },
        { name = "ERIEIRON_DB_NAME", value = var.DatabaseName },
        { name = "ERIEIRON_DB_HOST", value = var.RdsEndpointAddress },
        { name = "ERIEIRON_DB_PORT", value = var.RdsEndpointPort },
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

resource "aws_security_group_rule" "ecs_ingress" {
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
