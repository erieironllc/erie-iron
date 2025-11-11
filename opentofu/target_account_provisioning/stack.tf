# Target Account Bootstrap Stack
# Creates cross-account IAM role and permissions for Erie Iron self-driving coder agent

terraform {
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
  description = "Business name for resource naming and tagging"
  type        = string
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

# Data sources for current AWS context
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Cross-account IAM role for Erie Iron agent
resource "aws_iam_role" "erie_iron_target_account_agent_role" {
  name = "ErieIronTargetAccountAgentRole"
  
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        AWS = "arn:aws:iam::${var.control_plane_account_id}:root"
      }
      Action = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "sts:ExternalId" = var.external_id
        }
      }
    }]
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
  name = "ErieIronAgentPermissions"
  role = aws_iam_role.erie_iron_target_account_agent_role.id
  
  policy = templatefile("${path.module}/target_account_agent_permissions.json.tftpl", {
    account_id                   = var.target_account_id
    region                      = data.aws_region.current.name
    control_plane_account_id    = var.control_plane_account_id
    control_plane_region        = data.aws_region.current.name
  })
}

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