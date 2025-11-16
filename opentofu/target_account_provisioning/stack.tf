# Target Account Bootstrap Stack
# Creates cross-account IAM role and permissions for Erie Iron self-driving coder agent

terraform {

  backend "s3" {
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

# Variable definitions for target account setup
variable "control_plane_account_id" {
  description = "AWS account ID of the control plane"
  type        = string
}

variable "external_id" {
  description = "External ID for role assumption security"
  type        = string
  sensitive   = true
}

variable "business_name" {
  description = "Business name for resource naming and tagging (will be sanitized for S3 bucket naming)"
  type        = string
  
  validation {
    condition     = length(var.business_name) >= 1 && length(var.business_name) <= 50
    error_message = "Business name must be between 1 and 50 characters to ensure valid S3 bucket names."
  }
  
  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]$", var.business_name)) || length(var.business_name) == 1
    error_message = "Business name must contain only alphanumeric characters and hyphens, and cannot start or end with a hyphen (except for single character names)."
  }
}

variable "target_account_id" {
  description = "Target account ID for reference"
  type        = string
}

variable "env_type" {
  description = "Environment type (dev/production)"
  type        = string
  validation {
    condition     = contains(["dev", "production"], var.env_type)
    error_message = "The env_type value must be either 'dev' or 'production'."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.90.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets"
  type        = list(string)
  default     = ["10.90.0.0/20", "10.90.16.0/20"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets"
  type        = list(string)
  default     = ["10.90.32.0/20", "10.90.48.0/20"]
}

variable "enable_nat_gateway" {
  description = "Enable NAT Gateway (set to false for cost optimization in dev)"
  type        = bool
  default     = true
}

variable "single_nat_gateway" {
  description = "Use single NAT gateway instead of one per AZ for cost optimization"
  type        = bool
  default     = true
}

variable "enable_vpc_endpoints" {
  description = "Enable VPC endpoints for S3 and DynamoDB cost optimization"
  type        = bool
  default     = true
}

variable "cost_optimized_for_dev" {
  description = "Apply aggressive cost optimizations for development environments"
  type        = bool
  default     = true
}

# Data sources for current AWS context
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Get available availability zones
data "aws_availability_zones" "available" {
  state = "available"
}

# Local values for environment-specific cost optimization and naming
locals {
  # Apply cost optimizations based on environment and settings
  # enable_nat_gateway_final = var.env_type == "production" ? var.enable_nat_gateway : (var.cost_optimized_for_dev ? false : var.enable_nat_gateway)
  # single_nat_gateway_final = var.env_type == "dev" ? true : var.single_nat_gateway
  enable_nat_gateway_final = true
  single_nat_gateway_final = true
  enable_vpc_endpoints_final = var.enable_vpc_endpoints  # Always respect the setting
  
  # Calculate actual NAT gateway count
  nat_gateway_count = local.enable_nat_gateway_final ? (local.single_nat_gateway_final ? 1 : length(var.public_subnet_cidrs)) : 0
  
  # Sanitize business name for S3 bucket naming (lowercase, alphanumeric + hyphens only)
  sanitized_business_name = lower(replace(replace(var.business_name, "/[^a-zA-Z0-9-]/", ""), "/^-+|-+$/", ""))
  
  # S3 bucket name with validation
  state_bucket_name = "erieiron-opentofu-state-${local.sanitized_business_name}-${var.target_account_id}"
  
  # Validate final bucket name length (AWS S3 limit is 63 characters)
  bucket_name_valid = length(local.state_bucket_name) <= 63
}

# Validation check for bucket name length
resource "null_resource" "validate_bucket_name" {
  lifecycle {
    precondition {
      condition     = local.bucket_name_valid
      error_message = "Generated S3 bucket name '${local.state_bucket_name}' exceeds 63 character limit (${length(local.state_bucket_name)} chars). Please use a shorter business name."
    }
  }
}

# Cross-account IAM role for Erie Iron agent
resource "aws_iam_role" "erie_iron_target_account_agent_role" {
  name = "ErieIronTargetAccountAgentRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          AWS = [
            "arn:aws:iam::${var.control_plane_account_id}:role/xxbev-task-execution-role",
            "arn:aws:iam::${var.control_plane_account_id}:user/programatic-access",
            "arn:aws:iam::${var.target_account_id}:role/ErieIronTargetAccountAgentRole"
          ]
        }
        Action = "sts:AssumeRole"
        Condition = {
          StringEquals = {
            "sts:ExternalId" = var.external_id
          }
        }
      }
    ]
  })

  tags = {
    Business    = var.business_name
    Environment = var.env_type
    Purpose     = "ErieIronCrossAccountAccess"
    ManagedBy   = "ErieIron"
  }
}

# IAM policy attachment using permission template
resource "aws_iam_role_policy" "agent_permissions" {
  name = "ErieIronAgentPermissions-${var.target_account_id}"
  role = aws_iam_role.erie_iron_target_account_agent_role.id

  policy = templatefile("${path.module}/target_account_agent_permissions.json.tftpl", {
    account_id               = var.target_account_id
    region                   = data.aws_region.current.name
    control_plane_account_id = var.control_plane_account_id
    control_plane_region     = data.aws_region.current.name
    state_bucket_arn         = "arn:aws:s3:::${local.state_bucket_name}"
    state_bucket_objects_arn = "arn:aws:s3:::${local.state_bucket_name}/*"
  })
}

# DynamoDB table for OpenTofu/Terraform state locking
resource "aws_dynamodb_table" "opentofu_locks" {
  name         = "opentofu-locks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "OpenTofuStateLocking"
  }
}

# VPC
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "${var.business_name}-vpc"
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "SharedVPC"
  }
}

# Internet Gateway
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name        = "${var.business_name}-igw"
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "InternetGateway"
  }
}

# Public Subnets
resource "aws_subnet" "public" {
  count             = length(var.public_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.public_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]
  
  map_public_ip_on_launch = true

  tags = {
    Name        = "${substr(data.aws_availability_zones.available.names[count.index], -1, 1)}-public-subnet"
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "PublicSubnet"
    Type        = "public"
  }
}

# Private Subnets
resource "aws_subnet" "private" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name        = "${substr(data.aws_availability_zones.available.names[count.index], -1, 1)}-private-subnet"
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "PrivateSubnet"
    Type        = "private"
  }
}

# Elastic IP for NAT Gateway (conditionally created)
resource "aws_eip" "nat" {
  count  = local.nat_gateway_count
  domain = "vpc"

  depends_on = [aws_internet_gateway.main]

  tags = {
    Name        = local.single_nat_gateway_final ? "nat-gateway-eip" : "nat-gateway-eip-${count.index + 1}"
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "NatGatewayEIP"
    CostOptimization = local.single_nat_gateway_final ? "true" : "false"
  }
}

# NAT Gateway (conditionally created with flexible count)
resource "aws_nat_gateway" "main" {
  count         = local.nat_gateway_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[local.single_nat_gateway_final ? 0 : count.index].id

  depends_on = [aws_internet_gateway.main]

  tags = {
    Name        = local.single_nat_gateway_final ? "nat-gateway" : "nat-gateway-${count.index + 1}"
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "NatGateway"
    CostOptimization = local.single_nat_gateway_final ? "true" : "false"
  }
}

# Route table for public subnets
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name        = "route-table"
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "PublicRouteTable"
  }
}

# Route tables for private subnets (conditional NAT routing)
resource "aws_route_table" "private" {
  count  = length(var.private_subnet_cidrs)
  vpc_id = aws_vpc.main.id

  # Only add NAT route if NAT gateway is enabled
  dynamic "route" {
    for_each = local.enable_nat_gateway_final ? [1] : []
    content {
      cidr_block     = "0.0.0.0/0"
      nat_gateway_id = local.single_nat_gateway_final ? aws_nat_gateway.main[0].id : aws_nat_gateway.main[count.index].id
    }
  }

  tags = {
    Name        = "private-rt-${count.index + 1}"
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "PrivateRouteTable"
    CostOptimization = local.enable_nat_gateway_final ? (local.single_nat_gateway_final ? "true" : "false") : "max"
  }
}

# Associate public subnets with public route table
resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Associate private subnets with private route tables
resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# RDS Security Group
resource "aws_security_group" "rds" {
  name_prefix = "rds-securitygroup-"
  vpc_id      = aws_vpc.main.id
  description = "Security group for RDS instances"

  # Allow MySQL/Aurora access from private subnets
  ingress {
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    cidr_blocks = var.private_subnet_cidrs
    description = "MySQL/Aurora access from private subnets"
  }

  # Allow PostgreSQL access from private subnets
  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = var.private_subnet_cidrs
    description = "PostgreSQL access from private subnets"
  }

  # Allow all outbound traffic
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound traffic"
  }

  tags = {
    Name        = "rds-sg"
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "RDSSecurityGroup"
  }
}

# S3 VPC Gateway Endpoint (free, cost optimization)
resource "aws_vpc_endpoint" "s3" {
  count = local.enable_vpc_endpoints_final ? 1 : 0
  
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  
  route_table_ids = concat(
    [aws_route_table.public.id],
    aws_route_table.private[*].id
  )

  tags = {
    Name        = "${var.business_name}-s3-endpoint"
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "VPCEndpoint"
    Service     = "S3"
    CostOptimization = "true"
  }
}

# DynamoDB VPC Gateway Endpoint (free, cost optimization)
resource "aws_vpc_endpoint" "dynamodb" {
  count = local.enable_vpc_endpoints_final ? 1 : 0
  
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.dynamodb"
  vpc_endpoint_type = "Gateway"
  
  route_table_ids = concat(
    [aws_route_table.public.id],
    aws_route_table.private[*].id
  )

  tags = {
    Name        = "${var.business_name}-dynamodb-endpoint"
    Business    = var.business_name
    Environment = var.env_type
    ManagedBy   = "ErieIron"
    Purpose     = "VPCEndpoint"
    Service     = "DynamoDB"
    CostOptimization = "true"
  }
}

# Note: The S3 bucket for OpenTofu state is created dynamically by the bootstrap script
# using the pattern 'erieiron-opentofu-state-{business_name}-{target_account_id}'.
# Each target account has its own dedicated state bucket to avoid cross-account dependencies.
# The bucket is created via AWS CLI before OpenTofu initialization.

# Output values for credential storage
output "role_arn" {
  value       = aws_iam_role.erie_iron_target_account_agent_role.arn
  description = "ARN of the cross-account agent role"
}

output "external_id" {
  value       = var.external_id
  sensitive   = true
  description = "External ID for role assumption"
}

output "role_name" {
  value       = aws_iam_role.erie_iron_target_account_agent_role.name
  description = "Name of the cross-account agent role"
}

output "account_id" {
  value       = data.aws_caller_identity.current.account_id
  description = "Target account ID where role was created"
}

output "vpc_config" {
  value = {
    vpc_id = aws_vpc.main.id
    cidr_block = aws_vpc.main.cidr_block
    public_subnets = [for idx, subnet in aws_subnet.public : {
      name = subnet.tags["Name"]
      subnet_id = subnet.id
      cidr_block = subnet.cidr_block
      availability_zone = subnet.availability_zone
    }]
    private_subnets = [for idx, subnet in aws_subnet.private : {
      name = subnet.tags["Name"]
      subnet_id = subnet.id
      cidr_block = subnet.cidr_block
      availability_zone = subnet.availability_zone
    }]
    security_groups = {
      rds_security_group_id = aws_security_group.rds.id
    }
    vpc_endpoints = {
      s3_endpoint_id = local.enable_vpc_endpoints_final && length(aws_vpc_endpoint.s3) > 0 ? aws_vpc_endpoint.s3[0].id : null
      dynamodb_endpoint_id = local.enable_vpc_endpoints_final && length(aws_vpc_endpoint.dynamodb) > 0 ? aws_vpc_endpoint.dynamodb[0].id : null
      s3_endpoint_enabled = local.enable_vpc_endpoints_final
      dynamodb_endpoint_enabled = local.enable_vpc_endpoints_final
    }
    nat_gateway_config = {
      enabled = local.enable_nat_gateway_final
      single_nat_gateway = local.single_nat_gateway_final
      nat_gateway_count = local.nat_gateway_count
      nat_gateway_ids = local.enable_nat_gateway_final ? aws_nat_gateway.main[*].id : []
      private_route_table_ids = aws_route_table.private[*].id
      cost_optimization_level = local.enable_nat_gateway_final ? (local.single_nat_gateway_final ? "medium" : "none") : "maximum"
    }
    cost_optimization = {
      vpc_endpoints_enabled = local.enable_vpc_endpoints_final
      single_nat_gateway = local.single_nat_gateway_final
      nat_gateway_disabled = !local.enable_nat_gateway_final
      estimated_monthly_savings_usd = local.enable_nat_gateway_final ? (local.single_nat_gateway_final ? 45 : 0) : 90
    }
  }
  description = "VPC configuration for CloudAccount metadata including cost optimization settings"
}
