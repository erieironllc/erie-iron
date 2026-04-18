"""
Cognito Manager: JWT verification, token exchange, and user synchronization.

This module handles AWS Cognito authentication including:
- JWT token validation using JWKS
- OAuth authorization code exchange
- User synchronization from OAuth claims to Django users
"""
import json
import logging
import time
from typing import Optional, Dict, Any

import jwt
import requests
from django.contrib.auth import get_user_model
from django.db import transaction

from erieiron_autonomous_agent.models import OAuthAccount
from erieiron_common import common, view_utils

User = get_user_model()

# Cache JWKS for JWT verification (5 minute TTL)
_jwks_cache = {'data': None, 'expires': 0, 'user_pool_id': None}


def _get_cognito_config() -> Dict[str, Any]:
    config = view_utils.get_cognito_config()
    if not config.get("domain") or not config.get("client_id") or not config.get("user_pool_id"):
        raise ValueError("cognito configuration is incomplete")
    return config


def _get_cached_jwks(user_pool_id: str, region: str) -> Dict[str, Any]:
    """
    Fetch and cache Cognito JWKS (JSON Web Key Set) for JWT signature verification.

    JWKS contains the public keys used by Cognito to sign ID tokens.
    Cached for 5 minutes to minimize HTTP requests.

    Args:
        user_pool_id: Cognito User Pool ID
        region: AWS region

    Returns:
        dict: JWKS containing 'keys' array of public key objects

    Raises:
        requests.HTTPError: If JWKS endpoint returns error
    """
    now = time.time()

    if (_jwks_cache['data'] and
            _jwks_cache['expires'] > now and
            _jwks_cache['user_pool_id'] == user_pool_id):
        return _jwks_cache['data']

    jwks_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
    response = requests.get(jwks_url)
    response.raise_for_status()
    jwks = response.json()

    _jwks_cache['data'] = jwks
    _jwks_cache['expires'] = now + 300  # 5 minutes
    _jwks_cache['user_pool_id'] = user_pool_id

    return jwks


def _validate_and_parse_id_token(
        id_token: str,
        user_pool_id: str,
        client_id: str,
        region: str,
        nonce: Optional[str] = None
) -> Dict[str, Any]:
    """
    Validate Cognito ID token JWT signature and claims.

    Performs cryptographic verification:
    1. Decodes JWT header to get 'kid' (key ID)
    2. Fetches JWKS and finds matching public key
    3. Converts JWK to PEM format
    4. Verifies JWT signature using RS256 algorithm
    5. Validates standard claims (issuer, audience, expiration)
    6. Validates token_use='id' claim
    7. Validates nonce if provided

    Args:
        id_token: Cognito ID token (JWT string)
        user_pool_id: Expected Cognito User Pool ID
        client_id: Expected App Client ID (audience)
        region: AWS region
        nonce: Optional nonce from OAuth flow for replay protection

    Returns:
        dict: Validated JWT claims (email, sub, name, etc.)

    Raises:
        jwt.InvalidTokenError: If signature verification fails
        jwt.ExpiredSignatureError: If token is expired
        ValueError: If token_use != 'id' or nonce doesn't match
    """
    # Decode header to get 'kid' without verification
    unverified_header = jwt.get_unverified_header(id_token)
    kid = unverified_header['kid']

    # Fetch JWKS and find matching key
    jwks = _get_cached_jwks(user_pool_id, region)
    key_data = None
    for key in jwks['keys']:
        if key['kid'] == kid:
            key_data = key
            break

    if not key_data:
        raise ValueError(f"Public key with kid '{kid}' not found in JWKS")

    # Convert JWK to PEM format using PyJWT's RSAAlgorithm
    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))

    # Verify signature and decode claims
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    claims = jwt.decode(
        id_token,
        public_key,
        algorithms=['RS256'],
        audience=client_id,
        issuer=issuer,
        options={'verify_exp': True},
        leeway=10
    )

    # Validate token_use claim
    if claims.get('token_use') != 'id':
        raise ValueError(f"Invalid token_use: expected 'id', got '{claims.get('token_use')}'")

    # Validate nonce if provided (for web flows)
    if nonce and claims.get('nonce') != nonce:
        raise ValueError(f"Nonce mismatch: expected '{nonce}', got '{claims.get('nonce')}'")

    return claims


def verify_and_parse_id_token(id_token: str, nonce: Optional[str] = None) -> Dict[str, Any]:
    """
    Public API: Verify Cognito ID token and return validated claims.

    Loads Cognito configuration from Secrets Manager and validates the ID token.

    Args:
        id_token: Cognito ID token (JWT string)
        nonce: Optional nonce for replay protection

    Returns:
        dict: Validated JWT claims

    Raises:
        jwt.InvalidTokenError: If token validation fails
        ValueError: If configuration missing or claims invalid
    """
    config = _get_cognito_config()
    return _validate_and_parse_id_token(
        id_token,
        config['user_pool_id'],
        config['client_id'],
        config.get('region', "us-west-2"),
        nonce
    )


def exchange_code_for_tokens(
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None
) -> Dict[str, Any]:
    """
    Exchange OAuth authorization code for Cognito tokens.

    Calls Cognito token endpoint to exchange the authorization code
    received from OAuth callback for ID, access, and refresh tokens.

    Args:
        code: Authorization code from Cognito callback
        redirect_uri: Must match the redirect_uri used in authorization request
        code_verifier: Optional PKCE code verifier (for mobile flows)

    Returns:
        dict: Token response containing:
            - id_token: JWT ID token with user claims
            - access_token: Access token for API authorization
            - refresh_token: Refresh token for obtaining new access tokens
            - expires_in: Token lifetime in seconds

    Raises:
        requests.HTTPError: If token exchange fails
    """
    config = _get_cognito_config()
    
    cognito_domain = common.assert_not_empty(config['domain'], 'cognito config domain')
    token_url = f"{cognito_domain}/oauth2/token"

    data = {
        'grant_type': 'authorization_code',
        'client_id': config['client_id'],
        'code': code,
        'redirect_uri': redirect_uri,
    }

    if code_verifier:
        data['code_verifier'] = code_verifier

    response = requests.post(token_url, data=data)
    response.raise_for_status()

    return response.json()


def _sync_user_from_claims(claims: Dict[str, Any], provider: str = 'cognito-google') -> User:
    """
    Synchronize Django User from OAuth claims.

    Creates or updates OAuthAccount linking the external OAuth identity
    to a Django User. Uses database transactions with select_for_update()
    to prevent race conditions during concurrent logins.

    Args:
        claims: JWT claims dict (email, sub, given_name, family_name, picture, etc.)
        provider: OAuth provider name (default: 'cognito-google')

    Returns:
        User: Django User instance linked to this OAuth account

    Raises:
        KeyError: If required claims (email, sub) missing
    """
    email = claims['email']
    external_id = claims['sub']
    given_name = claims.get('given_name', '')
    family_name = claims.get('family_name', '')
    picture = claims.get('picture', '')

    with transaction.atomic():
        # Get or create OAuthAccount with lock to prevent race conditions
        oauth_account, created = OAuthAccount.objects.select_for_update().get_or_create(
            provider=provider,
            external_id=external_id,
            defaults={
                'raw_profile': claims
            }
        )

        # Update raw_profile on each login to capture any profile changes
        if not created:
            oauth_account.raw_profile = claims
            oauth_account.save(update_fields=['raw_profile', 'last_synced_at'])

        # Get or create Django User
        if oauth_account.user:
            user = oauth_account.user
        else:
            # Try to find existing user by email
            user, user_created = User.objects.get_or_create(
                username=email,
                defaults={
                    'email': email,
                    'first_name': given_name,
                    'last_name': family_name,
                }
            )

            # Link user to OAuth account
            oauth_account.user = user
            oauth_account.save(update_fields=['user'])

        # Update user profile from claims on each login
        user.email = email
        user.first_name = given_name
        user.last_name = family_name
        user.save(update_fields=['email', 'first_name', 'last_name'])

        if created:
            logging.info(f"New user created via {provider}: {user.email} (external_id: {external_id})")
        else:
            logging.info(f"User authenticated via {provider}: {user.email}")

    return user


def authenticate_user_from_id_token(id_token: str, nonce: Optional[str] = None) -> User:
    """
    Public API: Authenticate user from Cognito ID token.

    Main entry point for Cognito authentication. Validates the ID token
    and returns the authenticated Django User.

    Args:
        id_token: Cognito ID token (JWT string)
        nonce: Optional nonce for replay protection

    Returns:
        User: Authenticated Django User instance

    Raises:
        jwt.InvalidTokenError: If token validation fails
        ValueError: If token invalid or required claims missing
    """
    claims = verify_and_parse_id_token(id_token, nonce)
    user = _sync_user_from_claims(claims)
    return user
