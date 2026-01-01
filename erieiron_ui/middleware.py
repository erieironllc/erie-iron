import logging
from urllib.parse import quote

import jwt
from django.conf import settings
from django.http import HttpResponseRedirect


_LOG = logging.getLogger(__name__)


class HealthCheckBypassMiddleware:
    """
    Bypass Django's ALLOWED_HOSTS check for GET /health_check.
    Must be first in MIDDLEWARE so that no host validation or URL routing runs.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if str(request.path).startswith('/health') and request.method == 'GET':
            from erieiron_ui import views
            return views.healthcheck(request)

        return self.get_response(request)


class CognitoAuthMiddleware:
    """Cognito JWT authentication middleware with Google federation support."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._public_paths = {
            "/login",
            "/login/",
            "/logout",
            "/logout/",
            "/oauth/cognito/callback",
            "/oauth/cognito/callback/",
            "/health",
            "/health/",
        }
        self._static_prefix = f"/{settings.STATIC_URL.lstrip('/')}"

    def __call__(self, request):
        request.cognito_authenticated = False
        request.cognito_email = None
        request.cognito_sub = None
        request.cognito_user = None

        # Check for Cognito ID token in cookie
        id_token = request.COOKIES.get("cognito_id_token")
        if id_token:
            try:
                from erieiron_common import view_utils

                payload = view_utils.parse_session_token(id_token)
                if payload:
                    request.cognito_email = payload.get("email")
                    request.cognito_sub = payload.get("sub")
                    request.cognito_authenticated = bool(request.cognito_email and request.cognito_sub)

                    # Link to Person model if authenticated
                    if request.cognito_authenticated:
                        try:
                            from erieiron_common.models import Person
                            request.cognito_user = Person.objects.filter(cognito_sub=request.cognito_sub).first()
                        except Exception as user_exc:
                            _LOG.warning(f"Could not fetch Person for cognito_sub={request.cognito_sub}: {user_exc}")
            except Exception as exc:
                _LOG.exception(f"Failed to parse Cognito ID token: {exc}")

        if request.cognito_authenticated or self._is_public_path(request.path):
            return self.get_response(request)

        # Redirect to login
        login_path = "/login/"
        target = request.get_full_path()
        redirect_url = f"{login_path}?next={quote(target, safe='/#:?=&%')}"

        response = HttpResponseRedirect(redirect_url)
        response.delete_cookie("cognito_id_token")
        response.delete_cookie("cognito_access_token")
        response.delete_cookie("cognito_refresh_token")
        return response

    def _is_public_path(self, path: str) -> bool:
        if not path:
            return False

        if path in self._public_paths:
            return True

        if path.startswith(self._static_prefix):
            return True

        if path.startswith("/favicon"):
            return True

        return False


class SimpleAuthMiddleware:
    """DEPRECATED: Temporary JWT cookie auth. Use CognitoAuthMiddleware instead."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._public_paths = {
            settings.SIMPLE_AUTH_LOGIN_URL.rstrip("/"),
            settings.SIMPLE_AUTH_LOGIN_URL,
            settings.SIMPLE_AUTH_LOGOUT_URL.rstrip("/"),
            settings.SIMPLE_AUTH_LOGOUT_URL,
            "/health",
            "/health/",
        }
        self._static_prefix = f"/{settings.STATIC_URL.lstrip('/')}"

    def __call__(self, request):
        request.simple_auth_authenticated = False
        request.simple_auth_email = None

        token = request.COOKIES.get(settings.SIMPLE_AUTH_COOKIE_NAME)
        if token:
            try:
                payload = jwt.decode(
                    token,
                    settings.SIMPLE_AUTH_JWT_SECRET,
                    algorithms=["HS256"],
                    options={"require": ["email", "iat", "exp"]},
                )
                request.simple_auth_email = payload.get("email")
                request.simple_auth_authenticated = bool(request.simple_auth_email)
            except Exception as exc:
                _LOG.exception(exc)

        if request.simple_auth_authenticated or self._is_public_path(request.path):
            return self.get_response(request)

        login_path = settings.SIMPLE_AUTH_LOGIN_URL
        target = request.get_full_path()
        redirect_url = f"{login_path}?next={quote(target, safe='/#:?=&%')}"

        response = HttpResponseRedirect(redirect_url)
        response.delete_cookie(settings.SIMPLE_AUTH_COOKIE_NAME)
        return response

    def _is_public_path(self, path: str) -> bool:
        if not path:
            return False

        if path in self._public_paths:
            return True

        if path.startswith(self._static_prefix):
            return True

        if path.startswith("/favicon"):
            return True

        return False
