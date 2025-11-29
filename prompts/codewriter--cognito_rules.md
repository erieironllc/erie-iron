## Cognito Authentication Infrastructure Rules

When the task requires Cognito user authentication, you must add the following resources to the target application's `opentofu/application/stack.tf`.

### Required Variables

Add these variables if not already present:

```hcl
variable "EnableCognito" {
  description = "Whether to provision Cognito User Pool for authentication"
  type        = bool
  default     = false
}

variable "MobileAppScheme" {
  description = "Custom URL scheme for mobile app OAuth callbacks (e.g., 'myapp')"
  type        = string
  default     = ""
}
```

### Cognito User Pool Resources

Add these resources (conditionally created when `EnableCognito = true`):

```hcl
locals {
  base_tags = {
    Stack           = var.StackIdentifier
    ManagedBy       = "ErieIron"
    DeletePolicy    = var.DeletePolicy
    Environment     = var.Environment
  }
}

resource "aws_cognito_user_pool" "main" {
  count = var.EnableCognito ? 1 : 0
  name  = "${var.StackIdentifier}-user-pool"
  tags = local.base_tags

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

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  count        = var.EnableCognito ? 1 : 0
  domain       = var.StackIdentifier
  user_pool_id = aws_cognito_user_pool.main[0].id
}

resource "aws_cognito_user_pool_client" "main" {
  count        = var.EnableCognito ? 1 : 0
  name         = "${var.StackIdentifier}-client"
  user_pool_id = aws_cognito_user_pool.main[0].id

  generate_secret                      = false
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["email", "openid", "profile"]

  callback_urls = [
    "https://${var.DomainName}/oauth/cognito/web/callback",
    var.MobileAppScheme != "" ? "${var.MobileAppScheme}://oauth/cognito/callback" : null
  ]

  supported_identity_providers = ["COGNITO"]

  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH"
  ]
}
```

### Mobile App Config Secret

The secret must be created in OpenTofu to ensure atomicity with Cognito resources:

```hcl
resource "aws_secretsmanager_secret" "mobile_app_config" {
  count = var.EnableCognito ? 1 : 0
  name  = "${var.StackIdentifier}/mobile-app-config"

  lifecycle {
    prevent_destroy = ERIE_IRON_RETAIN_RESOURCES
  }
}

resource "aws_secretsmanager_secret_version" "mobile_app_config" {
  count     = var.EnableCognito ? 1 : 0
  secret_id = aws_secretsmanager_secret.mobile_app_config[0].id

  secret_string = jsonencode({
    cognito = {
      region         = data.aws_region.current.name
      userPoolId     = aws_cognito_user_pool.main[0].id
      clientId       = aws_cognito_user_pool_client.main[0].id
      domain         = "https://${aws_cognito_user_pool_domain.main[0].domain}.auth.${data.aws_region.current.name}.amazoncognito.com"
      redirectUri    = var.MobileAppScheme != "" ? "${var.MobileAppScheme}://oauth/cognito/callback" : ""
      webRedirectUri = "https://${var.DomainName}/oauth/cognito/web/callback"
    }
  })
}
```

### IAM Policy for Secret Access

Grant the ECS task role permission to read the Cognito secret:

```hcl
resource "aws_iam_role_policy" "web_cognito_secret" {
  count = var.EnableCognito ? 1 : 0
  name  = "${var.StackIdentifier}-web-cognito-secret"
  role  = aws_iam_role.web_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "AllowReadCognitoSecret"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.mobile_app_config[0].arn
    }]
  })
}
```

### ECS Container Environment Variable

Add to the container's environment array:

```hcl
# In aws_ecs_task_definition.web.container_definitions environment list:
{
  name  = "COGNITO_SECRET_ARN"
  value = var.EnableCognito ? aws_secretsmanager_secret.mobile_app_config[0].arn : ""
}
```

### Required Outputs

Add these outputs for orchestration visibility:

```hcl
output "CognitoUserPoolId" {
  description = "Cognito User Pool ID"
  value       = var.EnableCognito ? aws_cognito_user_pool.main[0].id : null
}

output "CognitoClientId" {
  description = "Cognito App Client ID"
  value       = var.EnableCognito ? aws_cognito_user_pool_client.main[0].id : null
}

output "CognitoDomain" {
  description = "Cognito hosted UI domain"
  value       = var.EnableCognito ? "https://${aws_cognito_user_pool_domain.main[0].domain}.auth.${data.aws_region.current.name}.amazoncognito.com" : null
}

output "CognitoSecretArn" {
  description = "ARN of the mobile app config secret containing Cognito settings"
  value       = var.EnableCognito ? aws_secretsmanager_secret.mobile_app_config[0].arn : null
  sensitive   = true
}
```

### Constraints

- **Namespace all resources** with `${var.StackIdentifier}` prefix
- **Use `ERIE_IRON_RETAIN_RESOURCES`** for lifecycle prevent_destroy
- **Cognito domain must be unique** across all AWS accounts - use StackIdentifier to ensure uniqueness
- **Do not hardcode** region, account ID, or domain names
- **Secret must be created in OpenTofu** (not post-apply) to ensure atomicity

### Application Code Pattern

When writing Python code that needs Cognito configuration, **always** use the `agent_tools` helper:

```python
from erieiron_public import agent_tools

# Fetch Cognito config (cached, with fallback to env vars)
cognito_config = agent_tools.get_cognito_config()

# Access values
user_pool_id = cognito_config.get("userPoolId")
client_id = cognito_config.get("clientId")
domain = cognito_config.get("domain")
```

**Important**:
- Do NOT implement custom secret fetching logic for Cognito config
- Do NOT read `COGNITO_SECRET_ARN` directly
- Always use `agent_tools.get_cognito_config()` which handles:
  - Secret fetching with caching
  - Fallback to individual env vars (`COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `COGNITO_DOMAIN`)
  - Force refresh via `agent_tools.get_cognito_config(force_refresh=True)`
