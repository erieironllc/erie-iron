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
