import logging
from urllib.parse import quote

import jwt
from django.conf import settings
from django.http import HttpResponseRedirect


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
    """Middleware that authenticates requests using Django session + Person model."""
    
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
        # Initialize attributes
        request.cognito_authenticated = False
        request.cognito_email = None
        request.cognito_sub = None
        request.cognito_user = None
        request.person = None
        
        # Skip auth for public paths
        if self._is_public_path(request.path):
            return self.get_response(request)
        
        # Use Django's session-based authentication
        if request.user.is_authenticated:
            # Load Person from session
            person_id = request.session.get('person_id')
            if person_id:
                from erieiron_common.models import Person
                try:
                    request.person = Person.objects.get(id=person_id)
                    request.cognito_user = request.person  # Backward compatibility
                    request.cognito_authenticated = True
                    request.cognito_email = request.person.email
                    request.cognito_sub = request.person.cognito_sub
                except Person.DoesNotExist:
                    logging.warning(f"Person {person_id} not found for authenticated user {request.user.email}")
            else:
                # Try to find Person by django_user FK
                from erieiron_common.models import Person
                try:
                    request.person = Person.objects.get(django_user=request.user)
                    request.cognito_user = request.person  # Backward compatibility
                    request.session['person_id'] = str(request.person.id)
                    request.cognito_authenticated = True
                    request.cognito_email = request.person.email
                    request.cognito_sub = request.person.cognito_sub
                except Person.DoesNotExist:
                    pass
        
        # Redirect to login if not authenticated
        if not request.cognito_authenticated:
            login_path = "/login/"
            target = request.get_full_path()
            redirect_url = f"{login_path}?next={quote(target, safe='/#:?=&%')}"
            return HttpResponseRedirect(redirect_url)
        
        return self.get_response(request)
    
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
                logging.exception(exc)
        
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
