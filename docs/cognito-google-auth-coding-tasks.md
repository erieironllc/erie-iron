# Cognito Google Auth: Remaining Coding Agent Tasks

This document identifies the **remaining work** needed to complete Cognito Google authentication integration. The OpenTofu infrastructure is **already provisioned** in `opentofu/application/stack.tf` (lines 785-1369).

## Task 1: Enhance Cognito Infrastructure (OpenTofu Modifications)

**Objective**: Complete missing pieces of Cognito infrastructure to fully support Google OAuth with profile data.

**Current State**: Basic Cognito infrastructure exists but lacks full attribute mapping and token configuration.

**Implementation Requirements**:

### 1.1: Add Custom Schema Attribute for Profile Picture

**Location**: `opentofu/application/stack.tf` - `aws_cognito_user_pool.main` resource (line 785)

**Add After**: `account_recovery_setting` block (line 799-804)

```hcl
  schema {
    name                = "picture"
    attribute_data_type = "String"
    mutable             = true
    string_attribute_constraints {
      min_length = 1
      max_length = 2048
    }
  }
```

**Rationale**: Google OAuth returns a `picture` URL claim that needs storage in Cognito user attributes.

### 1.2: Enhance Google Identity Provider Attribute Mapping

**Location**: `opentofu/application/stack.tf` - `aws_cognito_identity_provider.google` resource (line 926)

**Current Mapping** (line 939-944):
```hcl
  attribute_mapping = {
    email          = "email"
    email_verified = "email_verified"
    name           = "name"
    username       = "sub"
  }
```

**Replace With**:
```hcl
  attribute_mapping = {
    email          = "email"
    email_verified = "email_verified"
    name           = "name"
    username       = "sub"
    given_name     = "given_name"
    family_name    = "family_name"
    picture        = "picture"
  }
```

**Rationale**: Capture full name components and profile picture from Google OAuth.

### 1.3: Add Token Validity Configuration to App Client

**Location**: `opentofu/application/stack.tf` - `aws_cognito_user_pool_client.main` resource (line 821)

**Add After**: `explicit_auth_flows` block (line 841-844)

```hcl
  refresh_token_validity = 30
  access_token_validity  = 60
  id_token_validity      = 60

  token_validity_units {
    refresh_token = "days"
    access_token  = "minutes"
    id_token      = "minutes"
  }
```

**Rationale**: Explicitly control token lifetimes (30-day refresh, 60-min access/ID tokens).

### 1.4: Enhance Cognito Config Secret Content

**Location**: `opentofu/application/stack.tf` - `aws_secretsmanager_secret_version.cognito_config` resource (line 868)

**Current Content** (line 873-877):
```hcl
  secret_string = jsonencode({
    user_pool_id  = aws_cognito_user_pool.main.id
    client_id     = aws_cognito_user_pool_client.main.id
    client_secret = ""
  })
```

**Replace With**:
```hcl
  secret_string = jsonencode({
    user_pool_id     = aws_cognito_user_pool.main.id
    client_id        = aws_cognito_user_pool_client.main.id
    client_secret    = ""
    region           = data.aws_region.current.name
    domain           = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${data.aws_region.current.name}.amazoncognito.com"
    web_redirect_uri = "https://${var.DomainName}/oauth/cognito/callback"
    mobile_redirect_uri = var.MobileAppScheme != "" ? "${var.MobileAppScheme}://oauth/cognito/callback" : ""
  })
```

**Rationale**: Runtime code needs region, domain, and redirect URIs - storing them in the secret avoids hardcoding.

### 1.5: Add Localhost Callback URL for Development

**Location**: `opentofu/application/stack.tf` - `aws_cognito_user_pool_client.main` resource (line 821)

**Current Callback URLs** (line 830-833):
```hcl
  callback_urls = compact([
    "https://${var.DomainName}/oauth/cognito/callback",
    var.MobileAppScheme != "" ? "${var.MobileAppScheme}://oauth/cognito/callback" : null
  ])
```

**Replace With**:
```hcl
  callback_urls = compact([
    "https://${var.DomainName}/oauth/cognito/callback",
    "http://localhost:8024/oauth/cognito/callback",
    var.MobileAppScheme != "" ? "${var.MobileAppScheme}://oauth/cognito/callback" : null
  ])
```

**Rationale**: Enable local development testing without deploying to production domain.

**Success Criteria**:
- OpenTofu plan shows only additions (no resource replacements that would destroy user data)
- Custom `picture` attribute available in user pool schema
- Full attribute mapping from Google OAuth
- Token lifetimes explicitly configured
- Cognito config secret contains all required fields for runtime
- Localhost callback URL allows local dev testing

**Files to Modify**:
- `opentofu/application/stack.tf`

---

## Task 2: Create OAuthAccount Django Model

**Objective**: Add database model to link Cognito/Google identities to Django users.

**Current State**: Model does not exist.

**Implementation Requirements**:

### Add Model to `erieiron_autonomous_agent/models.py`

```python
class OAuthAccount(models.Model):
    """
    Links external OAuth provider accounts (e.g., Cognito/Google) to Django users.

    When a user authenticates via Cognito, we create an OAuthAccount record
    that maps their Cognito 'sub' claim to a Django User. This allows:
    - Multiple OAuth providers per user (future extensibility)
    - Tracking when users last authenticated
    - Storing raw OAuth profile data for debugging
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
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
        db_table = 'erieiron_oauth_account'
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
1. Add model to `erieiron_autonomous_agent/models.py`
2. Create migration: `python manage.py makemigrations`
3. **DO NOT** apply migration (manual operation per project rules)

**Architecture Compliance**:
- UUID primary key (project standard)
- Unique constraint on provider + external_id prevents duplicates
- Indexes for query performance

**Success Criteria**:
- Model defined with all required fields
- Migration file created (but not applied)
- `python manage.py check` succeeds

**Files to Modify**:
- `erieiron_autonomous_agent/models.py`
- New migration file in `erieiron_autonomous_agent/migrations/`

---

## Task 3: Create Cognito Manager Module

**Objective**: Implement JWT verification, token exchange, and user synchronization logic.

**Current State**: Module does not exist.

**Implementation Requirements**:

### Create `erieiron_autonomous_agent/cognito_manager.py`

**Core Functions**:

1. **`_get_cognito_config()`**:
   - Load config from Secrets Manager via `COGNITO_SECRET_ARN` env var
   - Cache for 5 minutes
   - Return dict with: user_pool_id, client_id, region, domain, web_redirect_uri, mobile_redirect_uri

2. **`_get_cached_jwks(user_pool_id, region)`**:
   - Fetch JWKS from `https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json`
   - Cache for 5 minutes

3. **`_validate_and_parse_id_token(id_token, user_pool_id, client_id, region, nonce=None)`**:
   - Decode JWT header to get 'kid'
   - Fetch JWKS and find matching key
   - Convert JWK to PEM using `jwt.algorithms.RSAAlgorithm.from_jwk()`
   - Verify signature with RS256
   - Validate claims: issuer, audience, expiration, token_use='id'
   - Validate nonce if provided
   - Return claims dict

4. **`verify_and_parse_id_token(id_token, nonce=None)`**:
   - Public API wrapper
   - Load config and call internal validation

5. **`exchange_code_for_tokens(code, redirect_uri, code_verifier=None)`**:
   - POST to `{cognito_domain}/oauth2/token`
   - Include PKCE code_verifier if provided
   - Return tokens: id_token, access_token, refresh_token

6. **`_sync_user_from_claims(claims, provider='cognito-google')`**:
   - Extract: email, given_name, family_name, picture
   - Use `transaction.atomic()` with `select_for_update()`
   - Get or create OAuthAccount (unique on provider + external_id)
   - Update raw_profile on each login
   - Create or link Django User (email as username)
   - Update user fields from claims
   - Return User

7. **`authenticate_user_from_id_token(id_token, nonce=None)`**:
   - Public API: validate token and return user
   - Call verify_and_parse_id_token → _sync_user_from_claims

**Dependencies to Add**:
```txt
PyJWT[crypto]==2.8.0
cryptography==42.0.5
requests==2.31.0
```

**Error Handling**:
- Fail-fast: let exceptions propagate
- No try/except blocks around business logic
- Logging via `logging.info()`, `logging.error()`, `logging.exception()` directly

**Architecture Compliance**:
- Credentials via Secrets Manager ARN from environment
- No hardcoded secrets
- No defensive error handling
- Use select_for_update() to prevent race conditions

**Success Criteria**:
- `python manage.py check` succeeds
- Can import: `from erieiron_autonomous_agent.cognito_manager import authenticate_user_from_id_token`
- JWT validation uses cryptographic verification
- User sync handles concurrent requests safely

**Files to Create**:
- `erieiron_autonomous_agent/cognito_manager.py`

**Files to Modify**:
- `requirements.txt`

---

## Task 4: Configure Django Settings

**Objective**: Add Cognito settings and JWT authentication configuration.

**Current State**: Settings likely have basic AWS configuration but not Cognito-specific.

**Implementation Requirements**:

### Add to `settings.py`

```python
# Cognito Configuration
COGNITO_SECRET_ARN = os.getenv('COGNITO_SECRET_ARN')

# Django REST Framework (if not already present)
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
}

# Simple JWT Configuration
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

**Dependency to Add**:
```txt
djangorestframework-simplejwt==5.3.1
```

**Success Criteria**:
- Settings load without errors
- `python manage.py check` succeeds
- COGNITO_SECRET_ARN accessible from environment

**Files to Modify**:
- `settings.py`
- `requirements.txt`

---

## Task 5: Implement Web OAuth Flow Views

**Objective**: Create Django views for web-based Cognito OAuth with session authentication.

**Current State**: Views do not exist.

**Implementation Requirements**:

### Add to `erieiron_ui/views.py`

**Views**:

1. **`login_view(request)`**:
   - Generate random state and nonce (32 bytes URL-safe)
   - Store in session: `oauth_state`, `oauth_nonce`
   - Build Cognito URL with identity_provider=Google
   - Redirect to Cognito

2. **`auth_callback_view(request)`**:
   - Validate state parameter
   - Exchange code for tokens
   - Validate ID token with nonce
   - Authenticate user (calls cognito_manager.authenticate_user_from_id_token)
   - Create Django session (django.contrib.auth.login)
   - Redirect to dashboard/profile
   - Decorator: `@csrf_exempt` (OAuth callback has no CSRF token)

3. **`logout_view(request)`**:
   - Log out from Django session
   - Redirect to home

### Add URL Routes

```python
path('auth/login/', views.login_view, name='login'),
path('oauth/cognito/callback', views.auth_callback_view, name='cognito_callback'),
path('auth/logout/', views.logout_view, name='logout'),
```

**Error Handling**:
- Let exceptions propagate
- OAuth errors logged and returned as HttpResponseBadRequest

**Success Criteria**:
- Views pass `python manage.py check`
- Login redirects to Cognito Hosted UI
- Callback authenticates and creates session
- User and OAuthAccount records created

**Files to Modify**:
- `erieiron_ui/views.py`
- `erieiron_ui/urls.py`

---

## Task 6: Implement API OAuth Flow Views

**Objective**: Create REST API endpoints for mobile apps to exchange ID tokens for Django JWT tokens.

**Current State**: API views do not exist.

**Implementation Requirements**:

### Add to `erieiron_ui/views.py` or new API views module

**Views**:

1. **`CognitoGoogleAuthView(APIView)`**:
   - Permission: AllowAny
   - POST `/api/auth/cognito/google/`
   - Request: `{"id_token": "eyJ..."}`
   - Validate ID token
   - Generate Django JWT tokens (RefreshToken.for_user)
   - Response: `{"access": "...", "refresh": "...", "user": {...}}`

2. **`MobileConfigView(APIView)`**:
   - Permission: AllowAny
   - GET `/api/mobile/config/`
   - Load Cognito config
   - Response: `{"cognito": {...}}`

### Add URL Routes

```python
from rest_framework_simplejwt.views import TokenRefreshView

path('api/auth/cognito/google/', CognitoGoogleAuthView.as_view(), name='cognito_google_auth'),
path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
path('api/mobile/config/', MobileConfigView.as_view(), name='mobile_config'),
```

**Success Criteria**:
- API endpoints return correct responses
- Token exchange validates ID tokens
- Django JWT tokens work for authenticated API access

**Files to Modify**:
- `erieiron_ui/views.py`
- `erieiron_ui/urls.py`

---

## Task 7: Add Template Context Processor (Optional)

**Objective**: Make Cognito config available in Django templates for client-side JavaScript.

**Current State**: Context processor does not exist.

**Implementation Requirements**:

### Create `erieiron_ui/context_processors.py`

```python
from erieiron_autonomous_agent.cognito_manager import _get_cognito_config

def cognito_settings(request):
    """Add Cognito configuration to template context."""
    try:
        config = _get_cognito_config()
        return {'cognito': config}
    except Exception:
        return {'cognito': {}}
```

### Update `settings.py`

Add to `TEMPLATES[0]['OPTIONS']['context_processors']`:
```python
'erieiron_ui.context_processors.cognito_settings',
```

**Success Criteria**:
- Context processor loads without errors
- Cognito config accessible in templates as `{{ cognito.domain }}`

**Files to Create**:
- `erieiron_ui/context_processors.py` (if doesn't exist)

**Files to Modify**:
- `settings.py`

---

## Task 8: Security Hardening

**Objective**: Implement production security best practices.

**Current State**: Basic security exists but needs enhancements.

**Implementation Requirements**:

### 8.1: Add Lifecycle Protection to Cognito User Pool

**Location**: `opentofu/application/stack.tf` - `aws_cognito_user_pool.main` resource (line 785)

**Current Lifecycle** (line 810-813):
```hcl
  lifecycle {
    # TODO(operator): Set prevent_destroy = true via manual edit or separate retain-only stack for production retention
    prevent_destroy = false
  }
```

**Note**: This TODO indicates manual edit is expected for production. Document this requirement but **do not** change the code in this task.

### 8.2: Add CORS Configuration

**Add to `requirements.txt`**:
```txt
django-cors-headers==5.1.0
```

**Add to `settings.py`**:
```python
INSTALLED_APPS += ['corsheaders']

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    # ... existing middleware ...
]

CORS_ALLOWED_ORIGINS = [
    f'https://{os.getenv("DOMAIN_NAME")}',
]

if DEBUG:
    CORS_ALLOWED_ORIGINS += [
        'http://localhost:8024',
        'http://127.0.0.1:8024',
    ]
```

### 8.3: Add Rate Limiting to Auth Endpoints

**Add to `requirements.txt`**:
```txt
django-ratelimit==4.1.0
```

**Apply to auth views**:
```python
from django_ratelimit.decorators import ratelimit

@ratelimit(key='ip', rate='5/m', method='POST')
class CognitoGoogleAuthView(APIView):
    ...
```

### 8.4: Add HTTPS Enforcement (Production)

**Add to `settings.py`**:
```python
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
```

### 8.5: Add Authentication Event Logging

**In `cognito_manager.py` - `_sync_user_from_claims()` function**:

```python
if created:
    logging.info(f"New user created via {provider}: {user.email} (external_id: {external_id})")
else:
    logging.info(f"User authenticated via {provider}: {user.email}")
```

**Success Criteria**:
- `python manage.py check --deploy` passes
- Rate limiting applied
- HTTPS redirect enabled in production
- Authentication events logged

**Files to Modify**:
- `settings.py`
- `erieiron_autonomous_agent/cognito_manager.py`
- `erieiron_ui/views.py` (rate limiting decorator)
- `requirements.txt`

---

## Task Sequencing and Dependencies

**Recommended Execution Order**:

1. **Task 1** (OpenTofu Enhancements) → No blockers; blocks Tasks 3-8
2. **Task 2** (OAuthAccount Model) → No blockers; blocks Tasks 3, 5, 6
3. **Task 3** (Cognito Manager) → Requires Tasks 1, 2; blocks Tasks 5, 6
4. **Task 4** (Django Settings) → Requires Task 1; blocks Tasks 5, 6
5. **Task 5** (Web OAuth Views) → Requires Tasks 3, 4
6. **Task 6** (API OAuth Views) → Requires Tasks 3, 4
7. **Task 7** (Context Processor) → Requires Task 3; optional
8. **Task 8** (Security Hardening) → Requires Tasks 1, 3, 5, 6; final step

**Critical Integration Points**:

- **Google OAuth Secret**: Must exist in AWS Secrets Manager before deploying stack (or provide via GoogleOAuthClientId/GoogleOAuthClientSecret variables)
- **Database Migration**: Task 2 creates migration but does NOT apply it (manual operation)
- **Cognito Callback URL**: Must match exactly between OpenTofu config and Google Cloud Console
- **Environment Variables**: Stack sets `COGNITO_SECRET_ARN` and `OAUTH_GOOGLE_SECRET_ARN` at runtime

**Testing Checkpoints**:

- After Task 1: `tofu plan` shows only additions, no replacements
- After Task 3: Test JWT validation in Django shell
- After Task 5: Complete end-to-end web OAuth flow
- After Task 6: Test API token exchange
- After Task 8: Run `python manage.py check --deploy`

---

## Summary of Existing vs. New Work

### Already Complete (in stack.tf)
✓ Cognito User Pool (basic config)
✓ Cognito Hosted UI Domain
✓ Cognito App Client (basic config)
✓ Google OAuth secret management
✓ Google Identity Provider (basic mapping)
✓ Cognito config secret (basic content)
✓ IAM permissions for ECS task role
✓ Environment variables in ECS container
✓ Stack outputs

### New Work Required
□ Custom `picture` schema attribute
□ Enhanced attribute mapping (given_name, family_name, picture)
□ Token validity configuration
□ Enhanced Cognito config secret content
□ Localhost callback URL
□ OAuthAccount Django model
□ Cognito manager module with JWT verification
□ Django settings for Cognito and JWT
□ Web OAuth flow views
□ API OAuth flow views
□ Template context processor (optional)
□ Security hardening (CORS, rate limiting, HTTPS, logging)

The infrastructure foundation is solid; remaining work focuses on application-layer integration and security.
