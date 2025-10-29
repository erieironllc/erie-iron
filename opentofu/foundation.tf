# Erie Iron Foundation Infrastructure - OpenTofu Configuration
# This module creates the foundational AWS infrastructure including VPC, networking, 
# RDS database, ECS cluster, and core services

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
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

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS allocated storage in GB"
  type        = number
  default     = 20
}

# Get available AZs
data "aws_availability_zones" "available" {
  state = "available"
}

# VPC
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name        = "${var.project_name}-VPC-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# Internet Gateway
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name        = "${var.project_name}-IGW-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# Public Subnets
resource "aws_subnet" "public_1" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = {
    Name        = "${var.project_name}-PublicSubnet1-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

resource "aws_subnet" "public_2" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.2.0/24"
  availability_zone       = data.aws_availability_zones.available.names[1]
  map_public_ip_on_launch = true

  tags = {
    Name        = "${var.project_name}-PublicSubnet2-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# Route Table
resource "aws_route_table" "main" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name        = "${var.project_name}-RouteTable-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# Route table associations
resource "aws_route_table_association" "public_1" {
  subnet_id      = aws_subnet.public_1.id
  route_table_id = aws_route_table.main.id
}

resource "aws_route_table_association" "public_2" {
  subnet_id      = aws_subnet.public_2.id
  route_table_id = aws_route_table.main.id
}

# DB Subnet Group
resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnet-group-${var.environment_name}"
  subnet_ids = [aws_subnet.public_1.id, aws_subnet.public_2.id]

  tags = {
    Name        = "${var.project_name}-DBSubnetGroup-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# Security Group for RDS
resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds-sg-${var.environment_name}"
  description = "Security group for RDS PostgreSQL database"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.project_name}-RDS-SG-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# Generate random password for RDS
resource "random_password" "db_password" {
  length  = 16
  special = true
}

# Secrets Manager Secret for RDS credentials
resource "aws_secretsmanager_secret" "db_secret" {
  name        = "${var.project_name}-rds-secret-${var.environment_name}"
  description = "RDS credentials for ${var.project_name}"

  tags = {
    Project     = var.project_name
    Environment = var.environment_name
  }
}

resource "aws_secretsmanager_secret_version" "db_secret_version" {
  secret_id = aws_secretsmanager_secret.db_secret.id
  secret_string = jsonencode({
    username = "masteruser"
    password = random_password.db_password.result
  })
}

# RDS PostgreSQL Instance
resource "aws_db_instance" "main" {
  identifier     = "${lower(var.project_name)}-db-${var.environment_name}"
  engine         = "postgres"
  engine_version = "17.2"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "erieiron"
  username = "masteruser"
  password = random_password.db_password.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false

  backup_retention_period = 7
  backup_window          = "03:00-04:00"
  maintenance_window     = "Sun:04:00-Sun:05:00"

  deletion_protection = false
  skip_final_snapshot = false
  final_snapshot_identifier = "${lower(var.project_name)}-final-snapshot-${var.environment_name}-${formatdate("YYYY-MM-DD-hhmm", timestamp())}"

  tags = {
    Name        = "${var.project_name}-RDS-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# ECS Cluster
resource "aws_ecs_cluster" "main" {
  name = "AppCluster-${var.environment_name}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name        = "${var.project_name}-ECS-Cluster-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# ECR Repository for web service
resource "aws_ecr_repository" "webservice" {
  name                 = "erieiron-webservice-${var.environment_name}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${var.project_name}-ECR-Webservice-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# ECR Repository for message processor
resource "aws_ecr_repository" "messageprocessor" {
  name                 = "erieiron-messageprocessor-${var.environment_name}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${var.project_name}-ECR-MessageProcessor-${var.environment_name}"
    Project     = var.project_name
    Environment = var.environment_name
  }
}

# IAM Role for CodeBuild
resource "aws_iam_role" "codebuild_service_role" {
  name = "${var.project_name}-CodeBuild-ServiceRole-${var.environment_name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "codebuild.amazonaws.com"
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

# IAM Policy for CodeBuild
resource "aws_iam_role_policy" "codebuild_policy" {
  name = "${var.project_name}-CodeBuild-Policy-${var.environment_name}"
  role = aws_iam_role.codebuild_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload"
        ]
        Resource = [
          aws_ecr_repository.webservice.arn,
          aws_ecr_repository.messageprocessor.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken"
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

# IAM Role for CodePipeline
resource "aws_iam_role" "codepipeline_service_role" {
  name = "${var.project_name}-CodePipeline-ServiceRole-${var.environment_name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "codepipeline.amazonaws.com"
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

# Outputs
output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "subnet_ids" {
  description = "Comma-separated public subnet IDs"
  value       = "${aws_subnet.public_1.id},${aws_subnet.public_2.id}"
}

output "subnet_id_list" {
  description = "List of public subnet IDs"
  value       = [aws_subnet.public_1.id, aws_subnet.public_2.id]
}

output "cluster_arn" {
  description = "ECS Cluster ARN"
  value       = aws_ecs_cluster.main.arn
}

output "cluster_name" {
  description = "ECS Cluster name"
  value       = aws_ecs_cluster.main.name
}

output "ecr_webservice_uri" {
  description = "ECR URI for webservice"
  value       = aws_ecr_repository.webservice.repository_url
}

output "ecr_messageprocessor_uri" {
  description = "ECR URI for message processor"
  value       = aws_ecr_repository.messageprocessor.repository_url
}

output "database_endpoint" {
  description = "RDS database endpoint"
  value       = aws_db_instance.main.endpoint
  sensitive   = true
}

output "database_secret_arn" {
  description = "ARN of the database credentials secret"
  value       = aws_secretsmanager_secret.db_secret.arn
}

output "codebuild_service_role_arn" {
  description = "CodeBuild service role ARN"
  value       = aws_iam_role.codebuild_service_role.arn
}

output "codepipeline_service_role_arn" {
  description = "CodePipeline service role ARN"
  value       = aws_iam_role.codepipeline_service_role.arn
}