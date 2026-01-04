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
  default     = 2048
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

variable "GoogleOAuthClientId" {
  description = "Google OAuth Client ID for Cognito federation"
  type        = string
  default     = ""
}

variable "GoogleOAuthClientSecret" {
  description = "Google OAuth Client Secret for Cognito federation"
  type        = string
  default     = ""
  sensitive   = true
}

variable "OauthGoogleSecretArn" {
  description = "ARN of USER_SUPPLIED secret containing Google OAuth credentials (client_id, client_secret). Managed via Erie Iron credential tooling. Invalid ARNs are treated as empty."
  type        = string
  default     = ""
  sensitive   = true
}

variable "GoogleOAuthEnabled" {
  description = "Whether to enable Google OAuth as identity provider. Defaults to true. Set to false only for automated testing purposes."
  type        = bool
  default     = true
}

variable "CognitoConfigSecretArn" {
  description = "ARN of an existing Cognito config secret to adopt. If provided and valid, the stack will not create a new secret. If empty or invalid, the stack creates an in-stack secret."
  type        = string
  default     = ""
  sensitive   = true
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

variable "MobileAppScheme" {
  description = "Custom URL scheme for mobile app OAuth callbacks (e.g., 'myapp')"
  type        = string
  default     = ""
}

# ============================================================================
# Data Sources
# ============================================================================

data "aws_region" "current" {}

data "aws_partition" "current" {}

data "aws_caller_identity" "current" {}

# ============================================================================
# Local Values
# ============================================================================

locals {
  hosted_zone_provided  = length(trim(var.DomainHostedZoneId, " ")) > 0
  database_name         = "appdb"
  foundation_identifier = coalesce(var.FoundationStackIdentifier, var.StackIdentifier)

  # Cognito config secret idempotency:
  # 1. If CognitoConfigSecretArn is provided and valid, adopt that existing secret (do not create)
  # 2. Otherwise, create an in-stack secret
  cognito_config_external_arn_valid = can(regex("^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:.+$", var.CognitoConfigSecretArn)) && length(trimspace(var.CognitoConfigSecretArn)) > 0 ? var.CognitoConfigSecretArn : ""
  cognito_config_create_secret      = length(trimspace(local.cognito_config_external_arn_valid)) == 0
  # Resolved secret ARN: either the in-stack created secret or the external ARN
  cognito_config_secret_arn = local.cognito_config_create_secret ? aws_secretsmanager_secret.cognito_config[0].arn : local.cognito_config_external_arn_valid

  # Google OAuth configuration - idempotent resolution with two paths:
  # Path 1: External ARN provided via OauthGoogleSecretArn (validated by regex only, primary idempotency mechanism)
  # Path 2: Create new in-stack secret when credentials provided and no external ARN
  # Versioned naming (-v3) ensures we can create new secrets even if prior versions are PENDING_DELETION
  google_oauth_external_arn_valid   = can(regex("^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:.+$", var.OauthGoogleSecretArn)) && length(trimspace(var.OauthGoogleSecretArn)) > 0 ? var.OauthGoogleSecretArn : ""
  google_oauth_credentials_provided = length(trimspace(var.GoogleOAuthClientId)) > 0 && length(trimspace(var.GoogleOAuthClientSecret)) > 0
  # Create secret only when: credentials provided AND no external ARN supplied
  google_oauth_create_secret = local.google_oauth_credentials_provided && length(local.google_oauth_external_arn_valid) == 0
  # Final resolved ARN: external ARN takes precedence, otherwise use newly created secret
  google_oauth_secret_arn = length(local.google_oauth_external_arn_valid) > 0 ? local.google_oauth_external_arn_valid : try(aws_secretsmanager_secret.google_oauth[0].arn, null)
  # Enable Google OAuth if: parameter is true AND (secret ARN exists OR credentials are provided)
  google_oauth_enabled    = var.GoogleOAuthEnabled 

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
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
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
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
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
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
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
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
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
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
  }
}

resource "aws_cloudwatch_log_group" "web" {
  name              = "/ecs/${var.StackIdentifier}-web"
  retention_in_days = 30

  lifecycle {
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
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
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
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
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
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
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
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
      },
      {
        Sid      = "AllowReadCognitoSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = local.cognito_config_secret_arn
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
        Sid    = "AllowReadLlmApiKeys"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret"
        ]
        Resource = "arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:LLM_API_KEYS*"
      }
    ]
  })

  lifecycle {
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
  }
}

resource "aws_iam_role_policy" "web_cognito_secret" {
  name = "${var.StackIdentifier}-web-cognito-secret"
  role = aws_iam_role.web_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "AllowReadCognitoSecret"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = local.cognito_config_secret_arn
    }]
  })
}

resource "aws_iam_role_policy" "web_google_oauth_secret" {
  count = local.google_oauth_enabled ? 1 : 0

  name = "${var.StackIdentifier}-web-google-oauth"
  role = aws_iam_role.web_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "AllowReadGoogleOAuthSecret"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = local.google_oauth_secret_arn
    }]
  })
}

resource "aws_iam_role_policy" "web_websocket" {
  name = "${var.StackIdentifier}-web-websocket"
  role = aws_iam_role.web_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowApiGatewayManagement"
        Effect   = "Allow"
        Action   = "execute-api:ManageConnections"
        Resource = "${aws_apigatewayv2_api.websocket.execution_arn}/*/*/@connections/*"
      },
      {
        Sid    = "AllowDynamoDBWebSocketConnections"
        Effect = "Allow"
        Action = [
          "dynamodb:Query",
          "dynamodb:GetItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [
          aws_dynamodb_table.websocket_connections.arn,
          "${aws_dynamodb_table.websocket_connections.arn}/index/person_id-index"
        ]
      }
    ]
  })

  lifecycle {
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
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
      environment = concat(
        [
          { name = "ALLOWED_HOSTS", value = "*" },
          { name = "RDS_SECRET_ARN", value = aws_db_instance.primary.master_user_secret[0].secret_arn },
          { name = "ERIEIRON_ENV", value = var.ErieIronEnv },
          { name = "ERIEIRON_DB_NAME", value = local.database_name },
          { name = "ERIEIRON_DB_HOST", value = aws_db_instance.primary.address },
          { name = "ERIEIRON_DB_PORT", value = tostring(aws_db_instance.primary.port) },
          { name = "STATIC_COMPILED_DIR", value = var.StaticCompiledDir },
          { name = "AWS_DEFAULT_REGION", value = data.aws_region.current.name },
          { name = "DOMAIN_NAME", value = var.DomainName },
          { name = "CLIENT_MESSAGE_WEBSOCKET_ENDPOINT", value = replace(replace(aws_apigatewayv2_stage.websocket.invoke_url, "wss://", ""), "ws://", "") },
          { name = "CLIENT_MESSAGE_DYNAMO_TABLE", value = aws_dynamodb_table.websocket_connections.name },
          { name = "COGNITO_SECRET_ARN", value = local.cognito_config_secret_arn }
        ],
        local.google_oauth_enabled ? [
          { name = "OAUTH_GOOGLE_SECRET_ARN", value = local.google_oauth_secret_arn }
        ] : []
      )
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
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
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
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
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
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
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
  description = "Whether to create the ECS ingress rule (useful when security group is shared). Set to false when the shared security group already has this rule."
  type        = bool
  default     = false
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

# ============================================================================
# Cognito Authentication Infrastructure
# ============================================================================

resource "aws_cognito_user_pool" "main" {
  name = "${var.StackIdentifier}-user-pool"

  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  schema {
    name                = "picture"
    attribute_data_type = "String"
    mutable             = true
    string_attribute_constraints {
      min_length = 1
      max_length = 2048
    }
  }

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-user-pool"
  })

  lifecycle {
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = var.StackIdentifier
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_cognito_user_pool_client" "main" {
  name         = "${var.StackIdentifier}-client"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret                      = false
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["email", "openid", "profile"]

  callback_urls = compact([
    "https://${var.DomainName}/oauth/cognito/callback",
    "http://localhost:8021/oauth/cognito/callback",
    "http://localhost:8022/oauth/cognito/callback",
    "http://localhost:8023/oauth/cognito/callback",
    var.MobileAppScheme != "" ? "${var.MobileAppScheme}://oauth/cognito/callback" : null
  ])

  logout_urls = compact([
    "https://${var.DomainName}/login/",
    "http://localhost:8021/login/",
    "http://localhost:8022/login/",
    "http://localhost:8023/login/"
  ])

  supported_identity_providers = local.google_oauth_enabled ? ["Google"] : ["COGNITO"]

  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH"
  ]

  read_attributes = [
    "email",
    "email_verified",
    "name",
    "given_name",
    "family_name",
    "picture"
  ]

  write_attributes = [
    "email",
    "name",
    "given_name",
    "family_name",
    "picture"
  ]

  refresh_token_validity = 30
  access_token_validity  = 60
  id_token_validity      = 60

  token_validity_units {
    refresh_token = "days"
    access_token  = "minutes"
    id_token      = "minutes"
  }

  depends_on = [aws_cognito_identity_provider.google]
}

resource "aws_secretsmanager_secret" "cognito_config" {
  count = local.cognito_config_create_secret ? 1 : 0

  # Use "-v3" suffix to avoid collisions with secrets scheduled for deletion
  # This ensures stack can deploy even if prior versions (-v2, base) are in deletion window
  name = "${var.StackIdentifier}/cognito-config-v3"

  recovery_window_in_days = var.DeletePolicy == "Delete" ? 0 : 7

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-cognito-config"
  })

  lifecycle {
    prevent_destroy = false
    ignore_changes  = [policy]
  }
}

resource "aws_secretsmanager_secret_version" "cognito_config" {
  count = local.cognito_config_create_secret ? 1 : 0

  secret_id = aws_secretsmanager_secret.cognito_config[0].id

  secret_string = jsonencode({
    user_pool_id        = aws_cognito_user_pool.main.id
    client_id           = aws_cognito_user_pool_client.main.id
    client_secret       = ""
    region              = data.aws_region.current.name
    domain              = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${data.aws_region.current.name}.amazoncognito.com"
    web_redirect_uri    = "https://${var.DomainName}/oauth/cognito/callback"
    mobile_redirect_uri = var.MobileAppScheme != "" ? "${var.MobileAppScheme}://oauth/cognito/callback" : ""
  })
}

# ============================================================================
# Google OAuth Secret (Idempotent Resolution)
# ============================================================================
# This block implements idempotent secret management:
# 1. If OauthGoogleSecretArn is provided (external ARN), use it - this is the primary idempotency path
# 2. Otherwise, create new in-stack secret with versioned naming
# 3. Versioned naming (-v3) avoids ResourceExistsException when prior versions are PENDING_DELETION
# 4. Orchestration layer can populate OauthGoogleSecretArn with ARN of existing secret for idempotent reuse

resource "aws_secretsmanager_secret" "google_oauth" {
  # Create in-stack secret only when:
  # - Client credentials are provided AND
  # - No external ARN is supplied
  count = local.google_oauth_create_secret ? 1 : 0

  # Use -v3 suffix to ensure we can create a new secret even if previous versions
  # (base, -v2) are in PENDING_DELETION state. This avoids ResourceExistsException.
  name = "${var.StackIdentifier}/google-oauth-config-v3"

  recovery_window_in_days = var.DeletePolicy == "Delete" ? 0 : 7

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-google-oauth-config"
  })

  lifecycle {
    prevent_destroy = false
    ignore_changes  = [policy]
  }
}

resource "aws_secretsmanager_secret_version" "google_oauth" {
  count = local.google_oauth_create_secret ? 1 : 0

  secret_id = aws_secretsmanager_secret.google_oauth[0].id

  secret_string = jsonencode({
    client_id     = var.GoogleOAuthClientId
    client_secret = var.GoogleOAuthClientSecret
  })
}

# ============================================================================
# Cognito Google Identity Provider
# ============================================================================

resource "aws_cognito_identity_provider" "google" {
  count = local.google_oauth_create_secret ? 1 : 0

  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = "Google"
  provider_type = "Google"

  provider_details = {
    authorize_scopes = "email openid profile"
    client_id        = var.GoogleOAuthClientId
    client_secret    = var.GoogleOAuthClientSecret
  }

  attribute_mapping = {
    email          = "email"
    email_verified = "email_verified"
    name           = "name"
    username       = "sub"
    given_name     = "given_name"
    family_name    = "family_name"
    picture        = "picture"
  }
}

# ============================================================================
# WebSocket Infrastructure
# ============================================================================

# DynamoDB table for WebSocket connection tracking
resource "aws_dynamodb_table" "websocket_connections" {
  name         = "${var.StackIdentifier}-websocket-connections"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "connection_id"

  attribute {
    name = "connection_id"
    type = "S"
  }

  attribute {
    name = "person_id"
    type = "S"
  }

  global_secondary_index {
    name            = "person_id-index"
    hash_key        = "person_id"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-websocket-connections"
  })

  lifecycle {
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
  }
}

# IAM role for WebSocket Lambda functions
resource "aws_iam_role" "websocket_lambda" {
  name = substr("${var.StackIdentifier}-websocket-lambda-role", 0, 64)

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-websocket-lambda-role"
  })

  lifecycle {
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
  }
}

# Attach basic Lambda execution policy
resource "aws_iam_role_policy_attachment" "websocket_lambda_basic" {
  role       = aws_iam_role.websocket_lambda.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# DynamoDB access policy for Lambda
resource "aws_iam_role_policy" "websocket_lambda_dynamodb" {
  name = "${var.StackIdentifier}-websocket-lambda-dynamodb"
  role = aws_iam_role.websocket_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowDynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:GetItem"
        ]
        Resource = aws_dynamodb_table.websocket_connections.arn
      }
    ]
  })
}

# Lambda function for WebSocket connect
data "archive_file" "websocket_connect" {
  type        = "zip"
  output_path = "${path.module}/.terraform/lambda-connect.zip"

  source {
    content  = <<-EOT
import json
import boto3
import os
from datetime import datetime, timedelta

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['TABLE_NAME'])

def lambda_handler(event, context):
    connection_id = event['requestContext']['connectionId']
    query_params = event.get('queryStringParameters', {}) or {}
    person_id = query_params.get('uid')

    if not person_id:
        return {'statusCode': 400, 'body': 'Missing uid parameter'}

    # Store connection with TTL (24 hours)
    ttl = int((datetime.utcnow() + timedelta(hours=24)).timestamp())

    table.put_item(Item={
        'connection_id': connection_id,
        'person_id': person_id,
        'ttl': ttl,
        'connected_at': datetime.utcnow().isoformat()
    })

    return {'statusCode': 200, 'body': 'Connected'}
EOT
    filename = "lambda_function.py"
  }
}

resource "aws_lambda_function" "websocket_connect" {
  filename         = data.archive_file.websocket_connect.output_path
  function_name    = "${var.StackIdentifier}-websocket-connect"
  role             = aws_iam_role.websocket_lambda.arn
  handler          = "lambda_function.lambda_handler"
  source_code_hash = data.archive_file.websocket_connect.output_base64sha256
  runtime          = "python3.11"
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.websocket_connections.name
    }
  }

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-websocket-connect"
  })

  lifecycle {
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
  }
}

# Lambda function for WebSocket disconnect
data "archive_file" "websocket_disconnect" {
  type        = "zip"
  output_path = "${path.module}/.terraform/lambda-disconnect.zip"

  source {
    content  = <<-EOT
import json
import boto3
import os

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['TABLE_NAME'])

def lambda_handler(event, context):
    connection_id = event['requestContext']['connectionId']

    table.delete_item(Key={'connection_id': connection_id})

    return {'statusCode': 200, 'body': 'Disconnected'}
EOT
    filename = "lambda_function.py"
  }
}

resource "aws_lambda_function" "websocket_disconnect" {
  filename         = data.archive_file.websocket_disconnect.output_path
  function_name    = "${var.StackIdentifier}-websocket-disconnect"
  role             = aws_iam_role.websocket_lambda.arn
  handler          = "lambda_function.lambda_handler"
  source_code_hash = data.archive_file.websocket_disconnect.output_base64sha256
  runtime          = "python3.11"
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.websocket_connections.name
    }
  }

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-websocket-disconnect"
  })

  lifecycle {
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
  }
}

# API Gateway WebSocket API
resource "aws_apigatewayv2_api" "websocket" {
  name                       = "${var.StackIdentifier}-websocket-api"
  protocol_type              = "WEBSOCKET"
  route_selection_expression = "$request.body.action"

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-websocket-api"
  })
}

# Connect route integration
resource "aws_apigatewayv2_integration" "connect" {
  api_id                    = aws_apigatewayv2_api.websocket.id
  integration_type          = "AWS_PROXY"
  integration_uri           = aws_lambda_function.websocket_connect.invoke_arn
  integration_method        = "POST"
  content_handling_strategy = "CONVERT_TO_TEXT"
}

resource "aws_apigatewayv2_route" "connect" {
  api_id    = aws_apigatewayv2_api.websocket.id
  route_key = "$connect"
  target    = "integrations/${aws_apigatewayv2_integration.connect.id}"
}

# Disconnect route integration
resource "aws_apigatewayv2_integration" "disconnect" {
  api_id                    = aws_apigatewayv2_api.websocket.id
  integration_type          = "AWS_PROXY"
  integration_uri           = aws_lambda_function.websocket_disconnect.invoke_arn
  integration_method        = "POST"
  content_handling_strategy = "CONVERT_TO_TEXT"
}

resource "aws_apigatewayv2_route" "disconnect" {
  api_id    = aws_apigatewayv2_api.websocket.id
  route_key = "$disconnect"
  target    = "integrations/${aws_apigatewayv2_integration.disconnect.id}"
}

# Deployment and stage
resource "aws_apigatewayv2_deployment" "websocket" {
  api_id = aws_apigatewayv2_api.websocket.id

  depends_on = [
    aws_apigatewayv2_route.connect,
    aws_apigatewayv2_route.disconnect
  ]

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_apigatewayv2_stage" "websocket" {
  api_id        = aws_apigatewayv2_api.websocket.id
  name          = var.ErieIronEnv
  deployment_id = aws_apigatewayv2_deployment.websocket.id

  default_route_settings {
    throttling_burst_limit = 5000
    throttling_rate_limit  = 10000
  }

  tags = merge(local.base_tags, {
    Name = "${var.StackIdentifier}-websocket-stage"
  })

  lifecycle {
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
  }
}

# Lambda permissions for API Gateway to invoke functions
resource "aws_lambda_permission" "websocket_connect" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.websocket_connect.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.websocket.execution_arn}/*/*"
}

resource "aws_lambda_permission" "websocket_disconnect" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.websocket_disconnect.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.websocket.execution_arn}/*/*"
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

# WebSocket outputs
output "WebSocketApiId" {
  description = "API Gateway WebSocket API identifier."
  value       = aws_apigatewayv2_api.websocket.id
}

output "WebSocketEndpoint" {
  description = "WebSocket endpoint URL (without stage name)."
  value       = aws_apigatewayv2_api.websocket.api_endpoint
}

output "WebSocketStageUrl" {
  description = "Full WebSocket URL including stage (use this for connections)."
  value       = "wss://${replace(aws_apigatewayv2_stage.websocket.invoke_url, "wss://", "")}"
}

output "WebSocketConnectionsTableName" {
  description = "DynamoDB table name for WebSocket connection tracking."
  value       = aws_dynamodb_table.websocket_connections.name
}

output "WebSocketConnectionsTableArn" {
  description = "DynamoDB table ARN for WebSocket connection tracking."
  value       = aws_dynamodb_table.websocket_connections.arn
}

# Cognito outputs
output "CognitoUserPoolId" {
  description = "Cognito User Pool ID"
  value       = aws_cognito_user_pool.main.id
}

output "CognitoClientId" {
  description = "Cognito App Client ID"
  value       = aws_cognito_user_pool_client.main.id
}

output "CognitoDomain" {
  description = "Cognito hosted UI domain"
  value       = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${data.aws_region.current.name}.amazoncognito.com"
}

output "CognitoSecretArn" {
  description = "ARN of the Cognito config secret (either in-stack created or external user-supplied)"
  value       = local.cognito_config_secret_arn
  sensitive   = true
}

output "GoogleOAuthSecretArn" {
  description = "ARN of the Google OAuth credentials secret (either in-stack created or external user-supplied) containing client_id and client_secret. Null if not configured."
  value       = local.google_oauth_secret_arn
  sensitive   = true
}

output "AwsRegion" {
  description = "AWS region being used by the provider."
  value       = data.aws_region.current.name
}
