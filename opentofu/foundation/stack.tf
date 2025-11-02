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
  description = "Application stack identifier (unused in foundation but provided for parity)."
  type        = string
  default     = null
}

variable "FoundationStackIdentifier" {
  description = "Identifier used to name foundation resources."
  type        = string
}

variable "ClientIpForRemoteAccess" {
  description = "Developer IPv4 address in /32 notation allowed to reach the database."
  type        = string
}

variable "DeletePolicy" {
  description = "Deletion policy hint (Retain/Delete)."
  type        = string
  default     = "Delete"
}

variable "DomainName" {
  description = "Primary domain used for SES configuration."
  type        = string
}

variable "DomainHostedZoneId" {
  description = "Route53 hosted zone that will store SES verification records."
  type        = string
  default     = ""
}

variable "VpcCidr" {
  description = "CIDR block of the shared VPC."
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

variable "PublicSubnet1Id" {
  description = "Public subnet ID for database subnet group."
  type        = string
}

variable "PublicSubnet2Id" {
  description = "Second public subnet ID for database subnet group."
  type        = string
}

variable "SecurityGroupId" {
  description = "Existing security group that will receive ingress rules for the database."
  type        = string
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

  base_tags = merge(
    {
      Name            = "${var.FoundationStackIdentifier}"
      StackIdentifier = var.FoundationStackIdentifier
    },
    var.tags
  )
}
data "aws_region" "current" {}

data "aws_partition" "current" {}

data "aws_caller_identity" "current" {}

resource "aws_db_subnet_group" "primary" {
  name       = "${var.FoundationStackIdentifier}-db-subnet-group"
  subnet_ids = [var.PublicSubnet1Id, var.PublicSubnet2Id]

  tags = merge(local.base_tags, {
    Name = "${var.FoundationStackIdentifier}-db-subnet-group"
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_db_instance" "primary" {
  identifier                  = "${var.FoundationStackIdentifier}-db"
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

  tags = merge(local.base_tags, {
    Name = "${var.FoundationStackIdentifier}-db-instance"
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
psql "host=${aws_db_instance.primary.address} user=$PGUSER dbname=${local.database_name}" \
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

output "FoundationStackIdentifier" {
  description = "Initiative-level identifier for persistent resources."
  value       = var.FoundationStackIdentifier
}

output "DomainHostedZoneId" {
  description = "Route53 hosted zone used for SES records."
  value       = var.DomainHostedZoneId
}

output "EffectiveHostedZoneId" {
  description = "Hosted zone ID used for DNS updates."
  value       = var.DomainHostedZoneId
}

output "RdsInstanceIdentifier" {
  description = "Identifier of the provisioned RDS instance."
  value       = aws_db_instance.primary.id
}

output "RdsInstanceEndpoint" {
  description = "Endpoint address of the RDS instance."
  value       = aws_db_instance.primary.address
}

output "RdsInstancePort" {
  description = "Endpoint port of the RDS instance."
  value       = aws_db_instance.primary.port
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
