# Implementing AWS Cognito with Google Federation in Django

This document provides step-by-step engineering tasks for implementing AWS Cognito authentication with Google identity federation in a Django application. Each task is designed to be executed independently via Claude Code with minimal drift.

The implementation covers infrastructure (Terraform/OpenTofu), Django backend, database models, and optionally mobile/web frontend integration.

---

## Prerequisites

Before starting, you'll need:
- Google OAuth 2.0 credentials (client ID and secret) from Google Cloud Console
- AWS account with permissions to create Cognito, Secrets Manager, and IAM resources
- Django project with PostgreSQL database
- OpenTofu/Terraform installed for infrastructure provisioning

---

## Task 1: Create Google OAuth Credentials

**Objective**: Set up Google OAuth 2.0 credentials for Cognito identity federation.

**Steps**:
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Navigate to "APIs & Services" > "Credentials"
4. Click "Create Credentials" > "OAuth 2.0 Client ID"
5. Configure consent screen if needed (application name, support email)
6. Select application type: "Web application"
7. Add authorized redirect URIs (you'll update these after Cognito setup):
   - Pattern: `https://{cognito-domain}.auth.{region}.amazoncognito.com/oauth2/idpresponse`
   - Example: `https://myapp123.auth.us-west-2.amazoncognito.com/oauth2/idpresponse`
8. Save the client ID and client secret
9. Store credentials in AWS Secrets Manager (we'll automate this in Task 2)

**Validation**:
- You have a Google OAuth client ID (format: `*.apps.googleusercontent.com`)
- You have a Google OAuth client secret

**Files Created**: None (manual setup)

---

## Task 2: Provision AWS Infrastructure with Terraform/OpenTofu

**Objective**: Create AWS Cognito User Pool, Google identity provider, app client, and Secrets Manager storage.

**Implementation**:

Create a new Terraform/OpenTofu file or add to your existing `stack.tf`:

```hcl
# Variables
variable "StackIdentifier" {
  description = "Unique identifier for this stack (e.g., 'myapp' or random string)"
  type        = string
}

variable "DomainName" {
  description = "Application domain name for OAuth callbacks"
  type        = string
}

variable "MobileScheme" {
  description = "Mobile app URI scheme for OAuth callbacks (e.g., 'myapp')"
  type        = string
  default     = ""
}

variable "OauthGoogleSecretArn" {
  description = "ARN of Secrets Manager secret containing Google OAuth credentials"
  type        = string
}

# Data source: Fetch Google OAuth credentials
data "aws_secretsmanager_secret_version" "google_oauth" {
  secret_id = var.OauthGoogleSecretArn
}

locals {
  google_oauth = jsondecode(data.aws_secretsmanager_secret_version.google_oauth.secret_string)
  region       = data.aws_region.current.name

  # Construct callback URLs
  web_callback_url    = "https://${var.DomainName}/oauth/cognito/web/callback"
  local_callback_url  = "http://localhost:8024/oauth/cognito/web/callback"
  mobile_callback_url = var.MobileScheme != "" ? "${var.MobileScheme}://oauth/cognito/callback" : ""

  # All callback URLs (filter empty strings)
  callback_urls = compact([
    local.web_callback_url,
    local.local_callback_url,
    local.mobile_callback_url
  ])
}

data "aws_region" "current" {}

# Cognito User Pool
resource "aws_cognito_user_pool" "main" {
  name = "${var.StackIdentifier}-user-pool"

  # Email is username, auto-verify email
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  # Password policy
  password_policy {
    minimum_length                   = 8
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  # Custom schema for Google profile picture
  schema {
    name                = "picture"
    attribute_data_type = "String"
    mutable             = true
    string_attribute_constraints {
      min_length = 1
      max_length = 2048
    }
  }

  # Account recovery
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  tags = {
    StackIdentifier = var.StackIdentifier
  }
}

# Google Identity Provider
resource "aws_cognito_identity_provider" "google" {
  user_pool_id  = aws_cognito_user_pool.main.id
  provider_name = "Google"
  provider_type = "Google"

  provider_details = {
    client_id        = local.google_oauth.client_id
    client_secret    = local.google_oauth.client_secret
    authorize_scopes = "profile email openid"
  }

  attribute_mapping = {
    email       = "email"
    username    = "sub"
    name        = "name"
    given_name  = "given_name"
    family_name = "family_name"
    picture     = "picture"
  }
}

# Cognito App Client
resource "aws_cognito_user_pool_client" "main" {
  name         = "${var.StackIdentifier}-app-client"
  user_pool_id = aws_cognito_user_pool.main.id

  # No client secret (public client for mobile/SPA)
  generate_secret = false

  # OAuth configuration
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["email", "openid", "profile"]
  callback_urls                        = local.callback_urls
  logout_urls                          = []
  supported_identity_providers         = ["COGNITO", "Google"]

  # Explicit auth flows
  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH"
  ]

  # Token validity
  refresh_token_validity = 30
  access_token_validity  = 60
  id_token_validity      = 60

  token_validity_units {
    refresh_token = "days"
    access_token  = "minutes"
    id_token      = "minutes"
  }

  # Prevent destruction if users exist
  lifecycle {
    prevent_destroy = false  # Set to true in production
  }
}

# Cognito Hosted UI Domain
resource "aws_cognito_user_pool_domain" "main" {
  domain       = var.StackIdentifier
  user_pool_id = aws_cognito_user_pool.main.id
}

# Secrets Manager: Store Cognito configuration for app consumption
resource "aws_secretsmanager_secret" "mobile_app_config" {
  name        = "${var.StackIdentifier}/mobile-app-config"
  description = "Cognito configuration for mobile and web apps"

  tags = {
    StackIdentifier = var.StackIdentifier
  }
}

resource "aws_secretsmanager_secret_version" "mobile_app_config" {
  secret_id = aws_secretsmanager_secret.mobile_app_config.id
  secret_string = jsonencode({
    cognito = {
      region         = local.region
      userPoolId     = aws_cognito_user_pool.main.id
      clientId       = aws_cognito_user_pool_client.main.id
      domain         = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${local.region}.amazoncognito.com"
      redirectUri    = local.mobile_callback_url
      webRedirectUri = local.web_callback_url
    }
  })
}

# Outputs
output "cognito_user_pool_id" {
  value       = aws_cognito_user_pool.main.id
  description = "Cognito User Pool ID"
}

output "cognito_client_id" {
  value       = aws_cognito_user_pool_client.main.id
  description = "Cognito App Client ID"
}

output "cognito_domain" {
  value       = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${local.region}.amazoncognito.com"
  description = "Cognito Hosted UI domain"
}

output "cognito_secret_arn" {
  value       = aws_secretsmanager_secret.mobile_app_config.arn
  description = "ARN of Secrets Manager secret containing Cognito config"
}
```

**Steps**:
1. Create a Secrets Manager secret containing Google OAuth credentials:
   ```bash
   aws secretsmanager create-secret \
     --name "google-oauth-credentials" \
     --secret-string '{"client_id":"YOUR_GOOGLE_CLIENT_ID","client_secret":"YOUR_GOOGLE_CLIENT_SECRET"}'
   ```
2. Add the above Terraform configuration to your infrastructure code
3. Create a `terraform.tfvars` file:
   ```hcl
   StackIdentifier        = "myapp123"  # Use unique identifier
   DomainName             = "myapp.com"
   MobileScheme           = "myapp"     # Optional, for mobile apps
   OauthGoogleSecretArn   = "arn:aws:secretsmanager:REGION:ACCOUNT:secret:google-oauth-credentials-XXXXXX"
   ```
4. Run `tofu init` and `tofu plan`
5. Apply the infrastructure: `tofu apply`
6. Note the outputs (User Pool ID, Client ID, Domain, Secret ARN)
7. Update Google OAuth redirect URIs in Google Cloud Console with the Cognito domain from outputs

**Validation**:
- Run `tofu output` to verify all resources created
- Check AWS Console: Cognito User Pool exists with Google identity provider
- Verify Secrets Manager secret contains Cognito configuration

**Files Created/Modified**:
- `opentofu/stack.tf` (or `terraform/stack.tf`)
- `terraform.tfvars`

---

## Task 3: Grant ECS Task Role Permissions (if using ECS)

**Objective**: Allow Django application running in ECS to read Cognito configuration from Secrets Manager.

**Implementation**:

Add to your ECS task role policy:

```hcl
# Assuming you have an ECS task execution role
resource "aws_iam_role_policy" "ecs_task_secrets_access" {
  name = "${var.StackIdentifier}-ecs-secrets-access"
  role = aws_iam_role.ecs_task_role.id  # Your ECS task role

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          aws_secretsmanager_secret.mobile_app_config.arn
        ]
      }
    ]
  })
}
```

**Alternative for EC2/Lambda**: Add similar policy to instance role or Lambda execution role.

**Alternative for local development**: Use AWS credentials with `secretsmanager:GetSecretValue` permission.

**Validation**:
- Check IAM role in AWS Console has policy attached
- Test access: `aws secretsmanager get-secret-value --secret-id <secret-name>` from ECS task

**Files Created/Modified**:
- `opentofu/stack.tf` (IAM policy section)

---

## Task 4: Install Python Dependencies

**Objective**: Install required Python packages for Cognito integration.

**Implementation**:

Add to `requirements.txt`:

```txt
PyJWT[crypto]==2.8.0
cryptography==42.0.5
requests==2.31.0
djangorestframework-simplejwt==5.3.1
boto3==1.34.69
```

Install dependencies:

```bash
pip install -r requirements.txt
```

**Package purposes**:
- `PyJWT[crypto]`: JWT token parsing and signature verification
- `cryptography`: RSA key operations for JWKS verification
- `requests`: HTTP client for Cognito API calls
- `djangorestframework-simplejwt`: Django JWT authentication
- `boto3`: AWS SDK for Secrets Manager access

**Validation**:
- Run `pip list | grep -E "(PyJWT|cryptography|djangorestframework-simplejwt|boto3)"`
- All packages should be installed

**Files Created/Modified**:
- `requirements.txt`

---

## Task 5: Create Database Models

**Objective**: Create `OAuthAccount` model to link Cognito identities to Django users.

**Implementation**:

Create or update `<your_app>/models.py` (e.g., `core/models.py`):

```python
from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model

AUTH_USER_MODEL = settings.AUTH_USER_MODEL


class OAuthAccount(models.Model):
    """
    Links external OAuth provider accounts (e.g., Cognito/Google) to Django users.

    When a user authenticates via Cognito, we create an OAuthAccount record
    that maps their Cognito 'sub' claim to a Django User. This allows:
    - Multiple OAuth providers per user (future extensibility)
    - Tracking when users last authenticated
    - Storing raw OAuth profile data for debugging
    """
    user = models.ForeignKey(
        AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='oauth_accounts',
        help_text="Django user linked to this OAuth account"
    )

    provider = models.CharField(
        max_length=64,
        help_text="OAuth provider name (e.g., 'cognito-google')"
    )

    external_id = models.CharField(
        max_length=255,
        help_text="Provider's unique user ID (e.g., Cognito 'sub' claim)"
    )

    raw_profile = models.JSONField(
        default=dict,
        help_text="Raw OAuth claims/profile data from provider"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'core_oauth_account'
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'external_id'],
                name='unique_oauth_provider_external_id'
            )
        ]
        indexes = [
            models.Index(fields=['provider', 'external_id']),
            models.Index(fields=['user']),
        ]

    def __str__(self):
        return f"{self.provider}:{self.external_id} -> User {self.user_id}"
```

**Steps**:
1. Add the above model to your Django app
2. Create migration: `python manage.py makemigrations`
3. Review migration file to ensure correctness
4. Apply migration: `python manage.py migrate`

**Validation**:
- Run `python manage.py showmigrations` - should show new migration applied
- Check database: `\d core_oauth_account` in psql - table should exist with unique constraint

**Files Created/Modified**:
- `<your_app>/models.py`
- `<your_app>/migrations/XXXX_create_oauth_account.py`

---

## Task 6: Create Cognito Manager Module

**Objective**: Implement JWT verification, token exchange, and user synchronization logic.

**Implementation**:

Create `<your_app>/cognito_manager.py`:

```python
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlencode

import boto3
import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

User = get_user_model()

# JWKS cache: {cache_time: float, jwks: dict}
_JWKS_CACHE = {"cache_time": 0, "jwks": None}
JWKS_CACHE_TTL = 300  # 5 minutes

# Cognito config cache
_COGNITO_CONFIG_CACHE = {"cache_time": 0, "config": None}
CONFIG_CACHE_TTL = 300  # 5 minutes


def _get_cognito_config() -> Dict[str, str]:
    """
    Load Cognito configuration from AWS Secrets Manager with caching.

    Returns:
        dict: {
            'region': 'us-west-2',
            'userPoolId': 'us-west-2_abc123',
            'clientId': 'client-id',
            'domain': 'https://stackid.auth.region.amazoncognito.com',
            'redirectUri': 'app://oauth/cognito/callback',
            'webRedirectUri': 'https://domain.com/oauth/cognito/web/callback'
        }
    """
    now = time.time()

    # Return cached config if fresh
    if _COGNITO_CONFIG_CACHE["cache_time"] > now - CONFIG_CACHE_TTL:
        return _COGNITO_CONFIG_CACHE["config"]

    # Load from Secrets Manager
    secret_arn = settings.COGNITO_SECRET_ARN
    region = settings.AWS_DEFAULT_REGION

    client = boto3.client('secretsmanager', region_name=region)
    response = client.get_secret_value(SecretId=secret_arn)
    secret_data = json.loads(response['SecretString'])

    cognito_config = secret_data.get('cognito', {})

    # Cache the config
    _COGNITO_CONFIG_CACHE["config"] = cognito_config
    _COGNITO_CONFIG_CACHE["cache_time"] = now

    return cognito_config


def _get_cached_jwks(user_pool_id: str, region: str) -> Dict[str, Any]:
    """
    Fetch Cognito JWKS with caching.

    JWKS (JSON Web Key Set) contains public keys used to verify JWT signatures.
    We cache it to avoid hitting Cognito on every token validation.
    """
    now = time.time()

    # Return cached JWKS if fresh
    if _JWKS_CACHE["cache_time"] > now - JWKS_CACHE_TTL and _JWKS_CACHE["jwks"]:
        return _JWKS_CACHE["jwks"]

    # Fetch fresh JWKS
    jwks_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
    response = requests.get(jwks_url, timeout=10)
    response.raise_for_status()
    jwks = response.json()

    # Cache the JWKS
    _JWKS_CACHE["jwks"] = jwks
    _JWKS_CACHE["cache_time"] = now

    return jwks


def _validate_and_parse_id_token(
    id_token: str,
    user_pool_id: str,
    client_id: str,
    region: str,
    nonce: Optional[str] = None
) -> Dict[str, Any]:
    """
    Validate and parse a Cognito ID token using JWKS.

    Steps:
    1. Decode JWT header to get 'kid' (key ID)
    2. Fetch JWKS from Cognito
    3. Find matching public key by 'kid'
    4. Convert JWK to PEM format
    5. Verify JWT signature using public key
    6. Validate claims (issuer, audience, expiration, token_use)
    7. Optionally validate nonce (for web flows)

    Raises:
        jwt.InvalidTokenError: If token is invalid or expired
        ValueError: If token structure is invalid or claims don't match
    """
    # Decode header without verification to get 'kid'
    unverified_header = jwt.get_unverified_header(id_token)
    kid = unverified_header.get('kid')
    if not kid:
        raise ValueError("Token missing 'kid' in header")

    # Fetch JWKS
    jwks = _get_cached_jwks(user_pool_id, region)

    # Find matching key
    key_data = None
    for key in jwks.get('keys', []):
        if key.get('kid') == kid:
            key_data = key
            break

    if not key_data:
        raise ValueError(f"Public key not found for kid: {kid}")

    # Convert JWK to PEM format (RSA public key)
    # Cognito uses RSA keys with 'n' (modulus) and 'e' (exponent)
    from jwt.algorithms import RSAAlgorithm
    public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))

    # Verify and decode token
    expected_issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"

    try:
        claims = jwt.decode(
            id_token,
            public_key,
            algorithms=['RS256'],
            audience=client_id,
            issuer=expected_issuer,
            options={
                'verify_signature': True,
                'verify_exp': True,
                'verify_aud': True,
                'verify_iss': True,
            }
        )
    except jwt.ExpiredSignatureError:
        raise jwt.InvalidTokenError("Token has expired")
    except jwt.InvalidAudienceError:
        raise jwt.InvalidTokenError(f"Invalid audience. Expected {client_id}")
    except jwt.InvalidIssuerError:
        raise jwt.InvalidTokenError(f"Invalid issuer. Expected {expected_issuer}")

    # Validate token_use claim
    if claims.get('token_use') != 'id':
        raise ValueError(f"Invalid token_use: {claims.get('token_use')}")

    # Validate nonce if provided (web flows)
    if nonce and claims.get('nonce') != nonce:
        raise ValueError("Invalid nonce")

    # Ensure required claims exist
    required_claims = ['sub', 'email']
    for claim in required_claims:
        if claim not in claims:
            raise ValueError(f"Missing required claim: {claim}")

    return claims


def verify_and_parse_id_token(id_token: str, nonce: Optional[str] = None) -> Dict[str, Any]:
    """
    Public API: Validate Cognito ID token and return claims.

    Args:
        id_token: JWT ID token from Cognito
        nonce: Optional nonce to validate (for web flows)

    Returns:
        dict: JWT claims including 'sub', 'email', 'given_name', 'family_name', 'picture'

    Raises:
        jwt.InvalidTokenError: If token is invalid
        ValueError: If configuration or claims are invalid
    """
    config = _get_cognito_config()

    return _validate_and_parse_id_token(
        id_token=id_token,
        user_pool_id=config['userPoolId'],
        client_id=config['clientId'],
        region=config['region'],
        nonce=nonce
    )


def exchange_code_for_tokens(
    code: str,
    redirect_uri: str,
    code_verifier: Optional[str] = None
) -> Dict[str, str]:
    """
    Exchange OAuth authorization code for tokens via Cognito token endpoint.

    Args:
        code: Authorization code from OAuth callback
        redirect_uri: Must match the redirect_uri used in authorization request
        code_verifier: PKCE code verifier (required for mobile flows)

    Returns:
        dict: {
            'id_token': 'eyJ...',
            'access_token': 'eyJ...',
            'refresh_token': 'eyJ...',
            'token_type': 'Bearer',
            'expires_in': 3600
        }

    Raises:
        requests.HTTPError: If token exchange fails
    """
    config = _get_cognito_config()
    token_endpoint = f"{config['domain']}/oauth2/token"

    data = {
        'grant_type': 'authorization_code',
        'client_id': config['clientId'],
        'code': code,
        'redirect_uri': redirect_uri,
    }

    # Add PKCE verifier if provided
    if code_verifier:
        data['code_verifier'] = code_verifier

    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    response = requests.post(token_endpoint, data=data, headers=headers, timeout=10)
    response.raise_for_status()

    return response.json()


def _sync_user_from_claims(claims: Dict[str, Any], provider: str = "cognito-google") -> User:
    """
    Create or update Django user from Cognito JWT claims.

    This function:
    1. Extracts user data from claims (email, name, picture)
    2. Creates OAuthAccount if it doesn't exist (using provider + sub as unique key)
    3. Creates or links Django User
    4. Updates user profile data on each login

    Uses transaction with select_for_update() to prevent race conditions
    when multiple requests arrive simultaneously for new users.

    Args:
        claims: JWT claims from Cognito ID token
        provider: Provider name (default: "cognito-google")

    Returns:
        User: Django user instance
    """
    from <your_app>.models import OAuthAccount  # Replace <your_app> with your app name

    external_id = claims['sub']
    email = claims.get('email', '').lower()
    given_name = claims.get('given_name', '')
    family_name = claims.get('family_name', '')
    picture = claims.get('picture', claims.get('custom:picture', ''))

    with transaction.atomic():
        # Get or create OAuthAccount (unique on provider + external_id)
        oauth_account, created = OAuthAccount.objects.select_for_update().get_or_create(
            provider=provider,
            external_id=external_id,
            defaults={
                'raw_profile': claims,
            }
        )

        # Update raw profile on each login
        if not created:
            oauth_account.raw_profile = claims
            oauth_account.save(update_fields=['raw_profile', 'last_synced_at'])

        # Create or link user
        if oauth_account.user is None:
            # Check if user already exists with this email
            user = User.objects.filter(email=email).first()

            if user is None:
                # Create new user
                user = User.objects.create_user(
                    username=email,  # Use email as username
                    email=email,
                    first_name=given_name,
                    last_name=family_name,
                )

            # Link user to OAuth account
            oauth_account.user = user
            oauth_account.save(update_fields=['user'])
        else:
            user = oauth_account.user

            # Update user data from claims
            updated = False
            if user.email != email:
                user.email = email
                updated = True
            if user.first_name != given_name:
                user.first_name = given_name
                updated = True
            if user.last_name != family_name:
                user.last_name = family_name
                updated = True

            if updated:
                user.save(update_fields=['email', 'first_name', 'last_name'])

        # Update profile picture if you have a user profile model
        # Example (uncomment and adjust if needed):
        # if hasattr(user, 'profile') and picture:
        #     profile = user.profile
        #     if profile.profile_image_url != picture:
        #         profile.profile_image_url = picture
        #         profile.save(update_fields=['profile_image_url'])

    return user


def authenticate_user_from_id_token(id_token: str, nonce: Optional[str] = None) -> User:
    """
    Public API: Validate ID token and return authenticated Django user.

    This is the main entry point for Cognito authentication.
    Called from Django views after receiving an ID token.

    Args:
        id_token: JWT ID token from Cognito
        nonce: Optional nonce to validate

    Returns:
        User: Authenticated Django user

    Raises:
        jwt.InvalidTokenError: If token is invalid
        ValueError: If claims are invalid
    """
    claims = verify_and_parse_id_token(id_token, nonce=nonce)
    user = _sync_user_from_claims(claims)
    return user
```

**Important**: Replace `<your_app>` with your actual Django app name (e.g., `core`).

**Validation**:
- No syntax errors: `python manage.py check`
- Import test: `python manage.py shell -c "from <your_app>.cognito_manager import verify_and_parse_id_token"`

**Files Created/Modified**:
- `<your_app>/cognito_manager.py`

---

## Task 7: Configure Django Settings

**Objective**: Add Cognito configuration to Django settings and configure JWT authentication.

**Implementation**:

Update `settings.py`:

```python
import os

# AWS Configuration
AWS_DEFAULT_REGION = os.getenv('AWS_DEFAULT_REGION', 'us-west-2')

# Cognito Configuration
COGNITO_USER_POOL_ID = os.getenv('COGNITO_USER_POOL_ID')  # Fallback for local dev
COGNITO_CLIENT_ID = os.getenv('COGNITO_CLIENT_ID')        # Fallback for local dev
COGNITO_DOMAIN = os.getenv('COGNITO_DOMAIN')              # Fallback for local dev
COGNITO_SECRET_ARN = os.getenv('COGNITO_SECRET_ARN')      # Primary source (Secrets Manager)

# Authentication backends
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',  # Standard Django auth
]

# Django REST Framework configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
}

# Simple JWT configuration
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    'ROTATE_REFRESH_TOKENS': False,
    'BLACKLIST_AFTER_ROTATION': False,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}
```

**Environment variables** (add to `.env` or `.envrc`):

```bash
# AWS
AWS_DEFAULT_REGION=us-west-2

# Cognito (from Terraform outputs)
COGNITO_SECRET_ARN=arn:aws:secretsmanager:us-west-2:ACCOUNT:secret:myapp123/mobile-app-config-XXXXXX
COGNITO_USER_POOL_ID=us-west-2_abc123     # Optional fallback
COGNITO_CLIENT_ID=your-client-id           # Optional fallback
COGNITO_DOMAIN=https://myapp123.auth.us-west-2.amazoncognito.com  # Optional fallback

# Application
DOMAIN_NAME=myapp.com
```

**Validation**:
- Run `python manage.py check` - no errors
- Test settings: `python manage.py shell -c "from django.conf import settings; print(settings.COGNITO_SECRET_ARN)"`

**Files Created/Modified**:
- `settings.py`
- `.env` or `.envrc`

---

## Task 8: Create Authentication Views (Web Flow)

**Objective**: Implement web-based OAuth flow with session-based authentication.

**Implementation**:

Create or update `<your_app>/views.py`:

```python
import logging
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import login
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt

from <your_app>.cognito_manager import (
    _get_cognito_config,
    exchange_code_for_tokens,
    authenticate_user_from_id_token
)

logger = logging.getLogger(__name__)


@require_http_methods(["GET"])
def login_view(request):
    """
    Initiate OAuth login flow via Cognito Hosted UI.

    Flow:
    1. Generate random state and nonce
    2. Save state and nonce to session
    3. Redirect to Cognito Hosted UI with identity_provider=Google
    """
    # Generate OAuth state and nonce
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)

    # Store in session for validation on callback
    request.session['oauth_state'] = state
    request.session['oauth_nonce'] = nonce

    # Get Cognito configuration
    config = _get_cognito_config()

    # Build Cognito Hosted UI URL
    params = {
        'client_id': config['clientId'],
        'response_type': 'code',
        'scope': 'openid email profile',
        'redirect_uri': config['webRedirectUri'],
        'state': state,
        'nonce': nonce,
        'identity_provider': 'Google',  # Force Google login
    }

    auth_url = f"{config['domain']}/oauth2/authorize?{urlencode(params)}"

    return redirect(auth_url)


@csrf_exempt  # OAuth callback doesn't include CSRF token
@require_http_methods(["GET"])
def auth_callback_view(request):
    """
    Handle OAuth callback from Cognito.

    Flow:
    1. Validate state parameter
    2. Exchange authorization code for tokens
    3. Validate ID token with nonce
    4. Sync user from claims
    5. Create Django session
    6. Redirect to app
    """
    code = request.GET.get('code')
    state = request.GET.get('state')
    error = request.GET.get('error')

    # Handle OAuth errors
    if error:
        error_description = request.GET.get('error_description', 'Unknown error')
        logger.error(f"OAuth error: {error} - {error_description}")
        return HttpResponseBadRequest(f"Authentication failed: {error_description}")

    # Validate state
    session_state = request.session.get('oauth_state')
    if not state or state != session_state:
        logger.error("Invalid OAuth state")
        return HttpResponseBadRequest("Invalid state parameter")

    # Get nonce from session
    nonce = request.session.get('oauth_nonce')

    # Clear session OAuth data
    request.session.pop('oauth_state', None)
    request.session.pop('oauth_nonce', None)

    if not code:
        return HttpResponseBadRequest("Missing authorization code")

    try:
        # Exchange code for tokens
        config = _get_cognito_config()
        tokens = exchange_code_for_tokens(
            code=code,
            redirect_uri=config['webRedirectUri']
        )

        # Validate ID token and get user
        user = authenticate_user_from_id_token(
            id_token=tokens['id_token'],
            nonce=nonce
        )

        # Create Django session
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')

        logger.info(f"User {user.email} authenticated via Cognito")

        # Redirect to app (customize as needed)
        return redirect('/profile/')

    except Exception as e:
        logger.exception(f"Authentication error: {e}")
        return HttpResponseBadRequest(f"Authentication failed: {str(e)}")


@require_http_methods(["GET"])
def logout_view(request):
    """
    Log out user from Django session.

    Note: This does NOT log out from Cognito/Google.
    To implement full logout, redirect to Cognito logout endpoint:
    {cognito_domain}/logout?client_id={client_id}&logout_uri={logout_uri}
    """
    from django.contrib.auth import logout
    logout(request)
    return redirect('/')
```

**Important**: Replace `<your_app>` with your actual Django app name.

**URL routing** - Add to `<your_app>/urls.py`:

```python
from django.urls import path
from <your_app> import views

urlpatterns = [
    path('auth/login/', views.login_view, name='login'),
    path('oauth/cognito/web/callback', views.auth_callback_view, name='cognito_callback'),
    path('auth/logout/', views.logout_view, name='logout'),
]
```

**Validation**:
- No syntax errors: `python manage.py check`
- Start dev server: `python manage.py runserver`
- Navigate to `http://localhost:8024/auth/login/` - should redirect to Cognito
- **Note**: OAuth flow won't fully work locally without proper HTTPS and domain configuration. Test in deployed environment.

**Files Created/Modified**:
- `<your_app>/views.py`
- `<your_app>/urls.py`

---

## Task 9: Create API Authentication Views (Mobile/API Flow)

**Objective**: Implement API endpoints for mobile apps to exchange ID tokens for Django JWT tokens.

**Implementation**:

Add to `<your_app>/views.py`:

```python
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from <your_app>.cognito_manager import (
    authenticate_user_from_id_token,
    _get_cognito_config
)


class CognitoGoogleAuthView(APIView):
    """
    Exchange Cognito ID token for Django JWT tokens.

    Mobile apps:
    1. Initiate OAuth flow with Cognito
    2. Receive ID token from Cognito
    3. POST ID token to this endpoint
    4. Receive Django JWT access + refresh tokens

    Request:
        POST /api/auth/cognito/google/
        {
            "id_token": "eyJ..."
        }

    Response:
        {
            "access": "eyJ...",  // Django JWT access token (15 min)
            "refresh": "eyJ...", // Django JWT refresh token (30 days)
            "user": {
                "id": 1,
                "email": "user@example.com",
                "first_name": "John",
                "last_name": "Doe"
            }
        }
    """
    permission_classes = [AllowAny]

    def post(self, request):
        id_token = request.data.get('id_token')

        if not id_token:
            return Response(
                {'error': 'id_token is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Validate ID token and get user
            user = authenticate_user_from_id_token(id_token)

            # Generate Django JWT tokens
            refresh = RefreshToken.for_user(user)

            return Response({
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                }
            })

        except Exception as e:
            logger.exception(f"Cognito authentication error: {e}")
            return Response(
                {'error': f'Authentication failed: {str(e)}'},
                status=status.HTTP_401_UNAUTHORIZED
            )


class MobileConfigView(APIView):
    """
    Return Cognito configuration for mobile apps.

    Response:
        {
            "cognito": {
                "region": "us-west-2",
                "userPoolId": "us-west-2_abc123",
                "clientId": "client-id",
                "domain": "https://stackid.auth.region.amazoncognito.com",
                "redirectUri": "app://oauth/cognito/callback"
            }
        }
    """
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            config = _get_cognito_config()
            return Response({'cognito': config})
        except Exception as e:
            logger.exception(f"Failed to load Cognito config: {e}")
            return Response(
                {'error': 'Configuration unavailable'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
```

**URL routing** - Add to `<your_app>/urls.py`:

```python
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    # ... existing patterns ...

    # API authentication
    path('api/auth/cognito/google/', CognitoGoogleAuthView.as_view(), name='cognito_google_auth'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/mobile/config/', MobileConfigView.as_view(), name='mobile_config'),
]
```

**Validation**:
- No syntax errors: `python manage.py check`
- Test config endpoint: `curl http://localhost:8024/api/mobile/config/`
- Should return Cognito configuration

**Files Created/Modified**:
- `<your_app>/views.py`
- `<your_app>/urls.py`

---

## Task 10: Test Authentication Flow

**Objective**: Validate end-to-end authentication flow.

**Implementation**:

### Manual Testing (Web Flow):

1. **Start dev server**:
   ```bash
   python manage.py runserver 0.0.0.0:8024
   ```

2. **Test login redirect**:
   - Visit: `http://localhost:8024/auth/login/`
   - Should redirect to Cognito Hosted UI
   - Should show "Sign in with Google" button

3. **Complete OAuth flow**:
   - Click "Sign in with Google"
   - Authenticate with Google account
   - Should redirect back to `http://localhost:8024/oauth/cognito/web/callback`
   - Should create Django session and redirect to `/profile/`

4. **Verify user created**:
   ```bash
   python manage.py shell
   >>> from django.contrib.auth import get_user_model
   >>> User = get_user_model()
   >>> User.objects.all()
   >>> # Should show user with email from Google
   >>> from <your_app>.models import OAuthAccount
   >>> OAuthAccount.objects.all()
   >>> # Should show OAuthAccount linked to user
   ```

### API Testing (Mobile Flow):

1. **Get mobile config**:
   ```bash
   curl http://localhost:8024/api/mobile/config/
   ```

   Expected response:
   ```json
   {
     "cognito": {
       "region": "us-west-2",
       "userPoolId": "us-west-2_abc123",
       "clientId": "...",
       "domain": "https://...",
       "redirectUri": "..."
     }
   }
   ```

2. **Simulate mobile login** (requires obtaining real ID token via OAuth):
   ```bash
   # This requires completing OAuth flow in mobile app or Postman
   curl -X POST http://localhost:8024/api/auth/cognito/google/ \
     -H "Content-Type: application/json" \
     -d '{"id_token": "REAL_ID_TOKEN_HERE"}'
   ```

   Expected response:
   ```json
   {
     "access": "eyJ...",
     "refresh": "eyJ...",
     "user": {
       "id": 1,
       "email": "user@example.com",
       ...
     }
   }
   ```

3. **Test authenticated API access**:
   ```bash
   curl http://localhost:8024/api/some-protected-endpoint/ \
     -H "Authorization: Bearer ACCESS_TOKEN_HERE"
   ```

**Validation**:
- User can complete web OAuth flow successfully
- User record created in database
- OAuthAccount record created with correct provider and external_id
- API config endpoint returns Cognito configuration
- Mobile token exchange works (when tested with real ID token)
- JWT tokens work for authenticated API access

**Files Created/Modified**: None (testing only)

---

## Task 11: Add Template Context Processor (Optional - for Web Apps)

**Objective**: Make Cognito configuration available in Django templates.

**Implementation**:

Create `<your_app>/context_processors.py`:

```python
from <your_app>.cognito_manager import _get_cognito_config


def cognito_settings(request):
    """
    Add Cognito configuration to template context.

    Usage in templates:
        <a href="{% url 'login' %}">Login with Google</a>

        <script>
            const cognitoConfig = {
                domain: "{{ cognito.domain }}",
                clientId: "{{ cognito.clientId }}"
            };
        </script>
    """
    try:
        config = _get_cognito_config()
        return {
            'cognito': config
        }
    except Exception:
        return {
            'cognito': {}
        }
```

Update `settings.py`:

```python
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                '<your_app>.context_processors.cognito_settings',  # Add this
            ],
        },
    },
]
```

**Validation**:
- No syntax errors: `python manage.py check`
- Test in Django shell:
  ```python
  from django.template import Context, Template
  from django.test import RequestFactory
  from <your_app>.context_processors import cognito_settings

  request = RequestFactory().get('/')
  context = cognito_settings(request)
  print(context['cognito'])
  ```

**Files Created/Modified**:
- `<your_app>/context_processors.py`
- `settings.py`

---

## Task 12: Handle Profile Pictures (Optional)

**Objective**: Store and update user profile pictures from Google OAuth.

**Implementation**:

If you have a user profile model (e.g., `CuratorProfile`), update `cognito_manager.py`:

In the `_sync_user_from_claims()` function, add after user creation/update:

```python
def _sync_user_from_claims(claims: Dict[str, Any], provider: str = "cognito-google") -> User:
    # ... existing code ...

    # Update profile picture if you have a profile model
    if hasattr(user, 'curatorprofile'):  # Adjust to your profile model name
        profile = user.curatorprofile
        picture_url = claims.get('picture', claims.get('custom:picture', ''))

        if picture_url and profile.profile_image_url != picture_url:
            profile.profile_image_url = picture_url
            profile.save(update_fields=['profile_image_url'])

    return user
```

**Alternative**: If you don't have a profile model, add a `profile_image_url` field to your User model:

```python
# In your custom User model or create a migration to add to Django's User
class CustomUser(AbstractUser):
    profile_image_url = models.URLField(max_length=2048, blank=True)
```

Then update `_sync_user_from_claims()`:

```python
picture = claims.get('picture', claims.get('custom:picture', ''))
if picture and user.profile_image_url != picture:
    user.profile_image_url = picture
    user.save(update_fields=['profile_image_url'])
```

**Validation**:
- After OAuth login, check user profile in Django admin
- Profile picture URL should be populated from Google

**Files Created/Modified**:
- `<your_app>/cognito_manager.py`
- Possibly `<your_app>/models.py` (if adding profile_image_url field)

---

## Task 13: Security Hardening

**Objective**: Implement production security best practices.

**Implementation**:

### 1. Update Terraform lifecycle protection:

```hcl
resource "aws_cognito_user_pool" "main" {
  # ... existing config ...

  lifecycle {
    prevent_destroy = true  # Prevent accidental deletion
  }
}
```

### 2. Restrict CORS (if using API):

In `settings.py`:

```python
# Install: pip install django-cors-headers
INSTALLED_APPS += ['corsheaders']

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    # ... other middleware ...
]

# Production: Only allow your domains
CORS_ALLOWED_ORIGINS = [
    'https://myapp.com',
    'https://www.myapp.com',
]

# Development: Allow localhost
if DEBUG:
    CORS_ALLOWED_ORIGINS += [
        'http://localhost:8024',
        'http://127.0.0.1:8024',
    ]
```

### 3. Add rate limiting to auth endpoints:

```python
# Install: pip install django-ratelimit
from django_ratelimit.decorators import ratelimit

@ratelimit(key='ip', rate='5/m', method='POST')
class CognitoGoogleAuthView(APIView):
    # ... existing code ...
```

### 4. Enable HTTPS redirect in production:

```python
# settings.py
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
```

### 5. Rotate JWKS cache appropriately:

Already implemented in `cognito_manager.py` with 5-minute TTL. No changes needed.

### 6. Log authentication events:

Add to `_sync_user_from_claims()`:

```python
import logging
logger = logging.getLogger(__name__)

def _sync_user_from_claims(claims: Dict[str, Any], provider: str = "cognito-google") -> User:
    # ... existing code ...

    if created:
        logger.info(f"New user created via {provider}: {user.email} (external_id: {external_id})")
    else:
        logger.info(f"User authenticated via {provider}: {user.email}")

    return user
```

**Validation**:
- Run `python manage.py check --deploy` for security checks
- Verify HTTPS redirect works in production
- Check logs for authentication events

**Files Created/Modified**:
- `opentofu/stack.tf`
- `settings.py`
- `<your_app>/cognito_manager.py`
- `requirements.txt` (add `django-cors-headers`, `django-ratelimit`)

---

## Task 14: Mobile App Integration (Optional - React Native)

**Objective**: Implement OAuth flow in React Native mobile app.

**Implementation**:

This task provides a React Native implementation example. Adapt to your mobile framework.

### Install dependencies:

```bash
npm install react-native-app-auth expo-crypto expo-secure-store
```

### Create `AuthService.js`:

```javascript
import * as Crypto from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';

const API_BASE_URL = 'https://api.myapp.com';

class AuthService {
  constructor() {
    this.config = null;
  }

  async loadConfig() {
    if (this.config) return this.config;

    const response = await fetch(`${API_BASE_URL}/api/mobile/config/`);
    const data = await response.json();
    this.config = data.cognito;
    return this.config;
  }

  async generatePKCE() {
    // Generate random code verifier
    const randomBytes = await Crypto.getRandomBytesAsync(32);
    const codeVerifier = this.base64URLEncode(randomBytes);

    // Generate code challenge (SHA-256 hash)
    const digest = await Crypto.digestStringAsync(
      Crypto.CryptoDigestAlgorithm.SHA256,
      codeVerifier
    );
    const codeChallenge = this.base64URLEncode(digest);

    return { codeVerifier, codeChallenge };
  }

  base64URLEncode(str) {
    return str
      .toString('base64')
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=/g, '');
  }

  async loginWithGoogle() {
    const config = await this.loadConfig();
    const { codeVerifier, codeChallenge } = await this.generatePKCE();
    const state = await this.generateRandomString();

    // Save pending session
    await SecureStore.setItemAsync('pending_oauth', JSON.stringify({
      codeVerifier,
      state,
      timestamp: Date.now()
    }));

    // Build Cognito OAuth URL
    const params = new URLSearchParams({
      client_id: config.clientId,
      response_type: 'code',
      scope: 'openid email profile',
      redirect_uri: config.redirectUri,
      state: state,
      code_challenge: codeChallenge,
      code_challenge_method: 'S256',
      identity_provider: 'Google',
      prompt: 'select_account'
    });

    const authUrl = `${config.domain}/oauth2/authorize?${params}`;

    // Open browser (use react-native-app-auth or Linking)
    // Example with Linking:
    // Linking.openURL(authUrl);

    return authUrl;
  }

  async handleCognitoCallback({ code, state }) {
    // Retrieve pending session
    const pendingSession = await SecureStore.getItemAsync('pending_oauth');
    if (!pendingSession) throw new Error('No pending OAuth session');

    const { codeVerifier, state: savedState } = JSON.parse(pendingSession);

    // Validate state
    if (state !== savedState) throw new Error('Invalid OAuth state');

    // Clear pending session
    await SecureStore.deleteItemAsync('pending_oauth');

    // Exchange code for tokens with Cognito
    const config = await this.loadConfig();
    const tokenResponse = await fetch(`${config.domain}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        client_id: config.clientId,
        code: code,
        redirect_uri: config.redirectUri,
        code_verifier: codeVerifier
      })
    });

    const cognitoTokens = await tokenResponse.json();

    // Exchange Cognito ID token for Django JWT tokens
    const djangoResponse = await fetch(`${API_BASE_URL}/api/auth/cognito/google/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id_token: cognitoTokens.id_token })
    });

    const djangoTokens = await djangoResponse.json();

    // Save tokens
    await SecureStore.setItemAsync('access_token', djangoTokens.access);
    await SecureStore.setItemAsync('refresh_token', djangoTokens.refresh);

    return djangoTokens;
  }

  async generateRandomString() {
    const bytes = await Crypto.getRandomBytesAsync(32);
    return this.base64URLEncode(bytes);
  }

  async getAccessToken() {
    return await SecureStore.getItemAsync('access_token');
  }

  async logout() {
    await SecureStore.deleteItemAsync('access_token');
    await SecureStore.deleteItemAsync('refresh_token');
  }
}

export default new AuthService();
```

### Usage in app:

```javascript
import AuthService from './AuthService';

// Login button handler
async function handleLogin() {
  const authUrl = await AuthService.loginWithGoogle();
  // Open browser with authUrl
  // App will be redirected to: myapp://oauth/cognito/callback?code=...&state=...
}

// Deep link handler (app.json or AppDelegate)
Linking.addEventListener('url', async (event) => {
  const url = event.url;

  // Parse callback URL
  if (url.startsWith('myapp://oauth/cognito/callback')) {
    const params = new URLSearchParams(url.split('?')[1]);
    const code = params.get('code');
    const state = params.get('state');

    try {
      const tokens = await AuthService.handleCognitoCallback({ code, state });
      console.log('Logged in!', tokens);
      // Navigate to authenticated app
    } catch (error) {
      console.error('Login failed:', error);
    }
  }
});
```

**Validation**:
- Mobile app can initiate OAuth flow
- Cognito Hosted UI opens in browser
- User authenticates with Google
- App receives callback with code
- App exchanges code for Django JWT tokens
- Tokens stored securely in SecureStore

**Files Created/Modified**:
- `mobile/src/auth/AuthService.js`
- `mobile/App.js` (add deep link handling)
- `mobile/app.json` (configure URL scheme)

---

## Summary

You now have a complete AWS Cognito + Google federated authentication system for Django. The implementation includes:

1. ✅ AWS infrastructure (Cognito User Pool, Google IdP, App Client)
2. ✅ Django backend (JWT verification, user sync, database models)
3. ✅ Web OAuth flow (session-based authentication)
4. ✅ API OAuth flow (JWT token exchange for mobile apps)
5. ✅ Security hardening (HTTPS, rate limiting, CORS)
6. ✅ Mobile app integration (React Native example)

Each task is designed to be executed independently with minimal dependencies on other tasks. Follow the tasks in order for initial implementation, or execute specific tasks to add missing functionality.

## Troubleshooting

### Common Issues:

**Issue**: "Invalid redirect_uri"
- **Cause**: Callback URL in OAuth request doesn't match Cognito App Client configuration
- **Fix**: Verify `callback_urls` in Terraform match the URLs used in Django views

**Issue**: "Invalid token signature"
- **Cause**: JWKS cache stale or incorrect User Pool ID
- **Fix**: Clear JWKS cache (restart Django) or verify `COGNITO_USER_POOL_ID`

**Issue**: "Missing 'kid' in JWT header"
- **Cause**: Receiving wrong token type (access token instead of ID token)
- **Fix**: Ensure you're sending `id_token`, not `access_token`

**Issue**: OAuthAccount.DoesNotExist
- **Cause**: Database migration not applied
- **Fix**: Run `python manage.py migrate`

**Issue**: AWS credentials error
- **Cause**: Django app can't access Secrets Manager
- **Fix**: Verify IAM role has `secretsmanager:GetSecretValue` permission

**Issue**: CORS error from mobile app
- **Cause**: API domain not in `CORS_ALLOWED_ORIGINS`
- **Fix**: Add mobile API domain to Django CORS settings
