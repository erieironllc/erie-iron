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

resource "aws_ses_domain_identity" "this" {
  domain = var.DomainName

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_ses_domain_mail_from" "this" {
  domain                 = aws_ses_domain_identity.this.domain
  mail_from_domain       = "mail.${var.DomainName}"
  behavior_on_mx_failure = "UseDefaultValue"

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_ses_domain_dkim" "this" {
  domain = aws_ses_domain_identity.this.domain

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

# Guardrail: Avoid using unknown values in for_each keys.
# DKIM tokens are apply-time values, so we use static keys ["0","1","2"]
# to ensure deterministic planning per Dynamic Resource Key Guardrail.
resource "aws_route53_record" "dkim" {
  for_each = local.hosted_zone_provided ? {
    for k in ["0", "1", "2"] :
    k => aws_ses_domain_dkim.this.dkim_tokens[tonumber(k)]
  } : {}

  zone_id = var.DomainHostedZoneId
  name    = "${each.value}._domainkey.${var.DomainName}"
  type    = "CNAME"
  ttl     = 300
  records = ["${each.value}.dkim.amazonses.com"]

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_route53_record" "mx" {
  count           = local.hosted_zone_provided ? 1 : 0
  zone_id         = var.DomainHostedZoneId
  name            = var.DomainName
  type            = "MX"
  ttl             = 300
  records         = ["10 inbound-smtp.${data.aws_region.current.name}.amazonaws.com"]
  allow_overwrite = true

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_route53_record" "spf" {
  count   = local.hosted_zone_provided ? 1 : 0
  zone_id = var.DomainHostedZoneId
  name    = var.DomainName
  type    = "TXT"
  ttl     = 300
  records = ["v=spf1 include:amazonses.com -all"]

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_s3_bucket" "ses_inbound" {
  bucket = "${var.FoundationStackIdentifier}-ses-inbound"

  server_side_encryption_configuration {
    rule {
      apply_server_side_encryption_by_default {
        sse_algorithm = "AES256"
      }
    }
  }

  tags = merge(local.base_tags, {
    Name = "${var.FoundationStackIdentifier}-ses-inbound"
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_s3_bucket_versioning" "ses_inbound" {
  bucket = aws_s3_bucket.ses_inbound.id
  versioning_configuration {
    status = "Suspended"
  }
  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_s3_bucket_public_access_block" "ses_inbound" {
  bucket = aws_s3_bucket.ses_inbound.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_s3_bucket_policy" "ses_inbound" {
  bucket = aws_s3_bucket.ses_inbound.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSesInboundWrites"
        Effect = "Allow"
        Principal = {
          Service = "ses.amazonaws.com"
        }
        Action   = ["s3:PutObject"]
        Resource = format("%s/*", aws_s3_bucket.ses_inbound.arn)
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = data.aws_caller_identity.current.account_id
          }
        }
      }
    ]
  })

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_ssm_parameter" "ses_inbound_bucket_name" {
  name      = "/${var.FoundationStackIdentifier}/SesInboundBucketName"
  type      = "String"
  value     = aws_s3_bucket.ses_inbound.id
  overwrite = true

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_ssm_parameter" "ses_inbound_bucket_policy" {
  name      = "/${var.FoundationStackIdentifier}/SesInboundBucketPolicyId"
  type      = "String"
  value     = aws_s3_bucket_policy.ses_inbound.id
  overwrite = true

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_route53_record" "dmarc" {
  count   = local.hosted_zone_provided ? 1 : 0
  zone_id = var.DomainHostedZoneId
  name    = "_dmarc.${var.DomainName}"
  type    = "TXT"
  ttl     = 300
  records = ["v=DMARC1; p=quarantine; rua=mailto:postmaster@${var.DomainName}"]

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_route53_record" "verification" {
  count   = local.hosted_zone_provided ? 1 : 0
  zone_id = var.DomainHostedZoneId
  name    = "_amazonses.${var.DomainName}"
  type    = "TXT"
  ttl     = 300
  records = ["verification"]

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_ssm_parameter" "verification_record_name" {
  count = local.hosted_zone_provided ? 1 : 0

  name  = "/${var.FoundationStackIdentifier}/SesVerificationTxtRecordName"
  type  = "String"
  value = aws_route53_record.verification[0].fqdn

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_ses_receipt_rule_set" "this" {
  rule_set_name = "${var.FoundationStackIdentifier}-rs"

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_ses_receipt_rule" "ses_inbound" {
  name          = "${var.FoundationStackIdentifier}-digest-inbound"
  rule_set_name = aws_ses_receipt_rule_set.this.rule_set_name
  enabled       = true
  tls_policy    = "Optional"
  recipients    = [format("digest@%s", var.DomainName)]
  scan_enabled  = true

  s3_action {
    position          = 1
    bucket_name       = aws_s3_bucket.ses_inbound.bucket
    object_key_prefix = format("%s/", var.DomainName)
  }

  depends_on = [aws_s3_bucket_policy.ses_inbound]

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_ssm_parameter" "receipt_rule_set_name" {
  name  = "/${var.FoundationStackIdentifier}/SesReceiptRuleSetName"
  type  = "String"
  value = aws_ses_receipt_rule_set.this.rule_set_name

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_ssm_parameter" "ses_inbound_rule_name" {
  name      = "/${var.FoundationStackIdentifier}/SesInboundRuleName"
  type      = "String"
  value     = aws_ses_receipt_rule.ses_inbound.name
  overwrite = true

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
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
