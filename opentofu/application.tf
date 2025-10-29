# Erie Iron Application Infrastructure - OpenTofu Configuration
# This module creates the application layer including ECS services, load balancers,
# domain configuration, and associated resources

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# Variables for configuration
variable "environment_name" {
  description = "Deployment environment (dev, stage, prod)"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name for tagging"
  type        = string
  default     = "ErieIron"
}

variable "domain_name" {
  description = "Domain name for the application"
  type        = string
  default     = ""
}

# Foundation stack outputs (these should be passed from foundation stack)
variable "vpc_id" {
  description = "VPC ID from foundation stack"
  type        = string
}

variable "subnet_ids" {
  description = "List of subnet IDs from foundation stack"
  type        = list(string)
}

variable "cluster_name" {
  description = "ECS cluster name from foundation stack"
  type        = string
}

variable "ecr_webservice_uri" {
  description = "ECR repository URI for webservice"
  type        = string
}

variable "ecr_messageprocessor_uri" {
  description = "ECR repository URI for message processor"
  type        = string
}

variable "database_endpoint" {
  description = "RDS database endpoint"
  type        = string
}

variable "database_secret_arn" {
  description = "ARN of database credentials secret"
  type        = string
}

# Data sources
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# Security Group for ALB
resource "aws_security_group" "alb" {
  name        = "${var.project_name}-alb-sg-${var.environment_name}"
  description = "Security group for Application Load Balancer"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.project_name}-ALB-SG-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# Security Group for ECS Services
resource "aws_security_group" "ecs_services" {
  name        = "${var.project_name}-ecs-services-sg-${var.environment_name}"
  description = "Security group for ECS services"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8001
    to_port         = 8001
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    from_port   = 8001
    to_port     = 8001
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.main.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.project_name}-ECS-Services-SG-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# Get VPC data
data "aws_vpc" "main" {
  id = var.vpc_id
}

# Application Load Balancer
resource "aws_lb" "main" {
  name               = "${lower(var.project_name)}-alb-${var.environment_name}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.subnet_ids

  enable_deletion_protection = false

  tags = {
    Name        = "${var.project_name}-ALB-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# Target Group for Web Service
resource "aws_lb_target_group" "webservice" {
  name     = "${lower(var.project_name)}-webservice-tg-${var.environment_name}"
  port     = 8001
  protocol = "HTTP"
  vpc_id   = var.vpc_id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    interval            = 30
    path                = "/health/"
    matcher             = "200"
  }

  tags = {
    Name        = "${var.project_name}-Webservice-TG-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# ALB Listener
resource "aws_lb_listener" "main" {
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.webservice.arn
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# CloudWatch Log Groups
resource "aws_cloudwatch_log_group" "webservice" {
  name              = "${var.project_name}-webservice-${var.environment_name}"
  retention_in_days = 14

  tags = {
    Name        = "${var.project_name}-Webservice-Logs-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

resource "aws_cloudwatch_log_group" "messageprocessor" {
  name              = "${var.project_name}-messageprocessor-${var.environment_name}"
  retention_in_days = 14

  tags = {
    Name        = "${var.project_name}-MessageProcessor-Logs-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# IAM Role for ECS Task Execution
resource "aws_iam_role" "ecs_task_execution_role" {
  name = "${var.project_name}-ECSTaskExecutionRole-${var.environment_name}"

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

  tags = {
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# Attach the ECS task execution role policy
resource "aws_iam_role_policy_attachment" "ecs_task_execution_role_policy" {
  role       = aws_iam_role.ecs_task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# IAM Role for ECS Tasks
resource "aws_iam_role" "ecs_task_role" {
  name = "${var.project_name}-ECSTaskRole-${var.environment_name}"

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

  tags = {
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# IAM Policy for ECS Tasks (comprehensive AWS access for Erie Iron)
resource "aws_iam_role_policy" "ecs_task_policy" {
  name = "${var.project_name}-ECSTaskPolicy-${var.environment_name}"
  role = aws_iam_role.ecs_task_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = [
          var.database_secret_arn,
          "arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ses:SendEmail",
          "ses:SendRawEmail",
          "ses:GetSendStatistics",
          "ses:GetIdentityVerificationAttributes",
          "ses:VerifyEmailIdentity",
          "ses:VerifyDomainIdentity"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          "arn:aws:s3:::${lower(var.project_name)}-*",
          "arn:aws:s3:::${lower(var.project_name)}-*/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "cloudformation:DescribeStacks",
          "cloudformation:DescribeStackEvents",
          "cloudformation:DescribeStackResources",
          "cloudformation:CreateStack",
          "cloudformation:UpdateStack",
          "cloudformation:DeleteStack"
        ]
        Resource = "arn:aws:cloudformation:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:stack/${lower(var.project_name)}-*/*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecs:DescribeServices",
          "ecs:DescribeTasks",
          "ecs:ListTasks",
          "ecs:UpdateService"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      }
    ]
  })
}

# ECS Task Definition for Web Service
resource "aws_ecs_task_definition" "webservice" {
  family                   = "${var.project_name}-webservice-${var.environment_name}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "webservice"
      image     = "${var.ecr_webservice_uri}:latest"
      essential = true
      portMappings = [
        {
          containerPort = 8001
          protocol      = "tcp"
        }
      ]
      environment = [
        {
          name  = "ERIEIRON_ENV"
          value = var.environment_name
        },
        {
          name  = "AWS_REGION"
          value = data.aws_region.current.name
        },
        {
          name  = "DATABASE_URL"
          value = "postgresql://masteruser@${var.database_endpoint}:5432/erieiron"
        }
      ]
      secrets = [
        {
          name      = "DATABASE_PASSWORD"
          valueFrom = "${var.database_secret_arn}:password::"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.webservice.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = {
    Name        = "${var.project_name}-Webservice-TaskDef-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# ECS Task Definition for Message Processor
resource "aws_ecs_task_definition" "messageprocessor" {
  family                   = "${var.project_name}-messageprocessor-${var.environment_name}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_task_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "messageprocessor"
      image     = "${var.ecr_messageprocessor_uri}:latest"
      essential = true
      environment = [
        {
          name  = "ERIEIRON_ENV"
          value = var.environment_name
        },
        {
          name  = "AWS_REGION"
          value = data.aws_region.current.name
        },
        {
          name  = "DATABASE_URL"
          value = "postgresql://masteruser@${var.database_endpoint}:5432/erieiron"
        }
      ]
      secrets = [
        {
          name      = "DATABASE_PASSWORD"
          valueFrom = "${var.database_secret_arn}:password::"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.messageprocessor.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = {
    Name        = "${var.project_name}-MessageProcessor-TaskDef-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# ECS Service for Web Service
resource "aws_ecs_service" "webservice" {
  name            = "${var.project_name}-webservice-${var.environment_name}"
  cluster         = var.cluster_name
  task_definition = aws_ecs_task_definition.webservice.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.ecs_services.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.webservice.arn
    container_name   = "webservice"
    container_port   = 8001
  }

  depends_on = [aws_lb_listener.main]

  tags = {
    Name        = "${var.project_name}-Webservice-Service-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# ECS Service for Message Processor
resource "aws_ecs_service" "messageprocessor" {
  name            = "${var.project_name}-messageprocessor-${var.environment_name}"
  cluster         = var.cluster_name
  task_definition = aws_ecs_task_definition.messageprocessor.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.ecs_services.id]
    assign_public_ip = true
  }

  tags = {
    Name        = "${var.project_name}-MessageProcessor-Service-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# S3 Bucket for application assets
resource "aws_s3_bucket" "assets" {
  bucket = "${lower(var.project_name)}-assets-${var.environment_name}-${random_id.bucket_suffix.hex}"

  tags = {
    Name        = "${var.project_name}-Assets-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket = aws_s3_bucket.assets.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Route 53 Hosted Zone (conditional)
resource "aws_route53_zone" "main" {
  count = var.domain_name != "" ? 1 : 0
  name  = var.domain_name

  tags = {
    Name        = "${var.project_name}-HostedZone-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# Route 53 Record for ALB (conditional)
resource "aws_route53_record" "main" {
  count   = var.domain_name != "" ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}

# Outputs
output "load_balancer_dns" {
  description = "DNS name of the load balancer"
  value       = aws_lb.main.dns_name
}

output "load_balancer_zone_id" {
  description = "Zone ID of the load balancer"
  value       = aws_lb.main.zone_id
}

output "webservice_service_name" {
  description = "Name of the web service ECS service"
  value       = aws_ecs_service.webservice.name
}

output "messageprocessor_service_name" {
  description = "Name of the message processor ECS service"
  value       = aws_ecs_service.messageprocessor.name
}

output "assets_bucket_name" {
  description = "Name of the S3 assets bucket"
  value       = aws_s3_bucket.assets.bucket
}

output "hosted_zone_id" {
  description = "Route 53 hosted zone ID"
  value       = var.domain_name != "" ? aws_route53_zone.main[0].zone_id : ""
}

output "application_url" {
  description = "Application URL"
  value       = var.domain_name != "" ? "https://${var.domain_name}" : "https://${aws_lb.main.dns_name}"
}