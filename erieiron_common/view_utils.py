import json
import logging
import os
import tempfile
import uuid
from functools import wraps

import jwt
import requests
from django.db.models import Model
from django.http import HttpResponseRedirect, JsonResponse
from django.middleware import csrf
from django.shortcuts import render
from django.urls import reverse
from jwt import PyJWKClient
from pygments.formatters.html import HtmlFormatter

import settings
from erieiron_public import agent_tools
from erieiron_common import common, models, runtime_config
from erieiron_common.common import build_absolute_uri
from erieiron_common.enums import PersonAuthStatus, Role
from erieiron_common.json_encoder import ErieIronJSONEncoder

COOKIE_AGE = 10 * 365 * 24 * 60 * 60  # 10 years

COOKIE_SESSION_TOKEN = "sessiontoken"
COOKIE_REFRESH_TOKEN = "refresh_token"
COOKIE_AUTOMATED_TEST_UID = "_automated_test_uid"
COOKIE_NEXT_URL = "next"


def auth_required(function):
    @wraps(function)
    def wrap(request, *args, **kwargs):
        try:
            current_user = get_current_user(request)
        except:
            current_user = None
        
        auth_status = current_user.get_auth_status() \
            if current_user \
            else PersonAuthStatus.NOT_LOGGED_IN
        
        if PersonAuthStatus.NOT_LOGGED_IN.eq(auth_status):
            return redirect(
                reverse('view_marketing_signup'),
                cookies=[
                    (COOKIE_NEXT_URL, request.get_full_path())
                ]
            )
        elif PersonAuthStatus.BANNED.eq(auth_status):
            return redirect(reverse('logout'))
        elif PersonAuthStatus.NOT_INVITED.eq(auth_status):
            return redirect(reverse('view_marketing_signup'))
        elif PersonAuthStatus.INVITED_BUT_NOT_CONFIRMED.eq(auth_status):
            return redirect(reverse('view_signup_confirmation'))
        elif PersonAuthStatus.ALL_GOOD.eq(auth_status):
            return function(request, *args, **kwargs)
        else:
            raise ValueError(f"unhandled auth_status {auth_status}")
    
    return wrap


def token_required(function):
    @wraps(function)
    def wrap(request, *args, **kwargs):
        try:
            current_user = get_current_user(request)
            if current_user.has_role(Role.ADMIN):
                return function(request, *args, **kwargs)
        except:
            pass
        
        token = rget(request, "token")
        if not token:
            raise ValueError("invalid request")
        
        token_parts = token.split("__")
        if len(token_parts) != 2:
            raise ValueError("invalid request")
        
        token_key_lookup = token_parts[0]
        token_key_expected_value = token_parts[1]
        
        return function(request, *args, **kwargs)
    
    return wrap


def admin_required(function):
    @wraps(function)
    def wrap(request, *args, **kwargs):
        p = get_current_user(request)
        
        if p is None or not p.is_admin():
            return redirect(
                reverse('login'),
                cookies=[
                    (COOKIE_NEXT_URL, request.get_full_path())
                ]
            )
        else:
            return function(request, *args, **kwargs)
    
    return wrap


def get_from_post(request, field_name, current_val, changed_fields_tracker=None):
    val_from_req = common.get(request.POST, field_name, default_val=current_val)
    
    if common.is_not_equivalent(current_val, val_from_req) and changed_fields_tracker is not None:
        changed_fields_tracker.append(field_name)
    
    return val_from_req


def validate_context(context):
    for key, value in context.items():
        if contains_model(value):
            raise ValueError(f"Context key '{key}' contains a Django model instance.")


def contains_model(value):
    if isinstance(value, Model):
        return True
    elif isinstance(value, dict):
        return any(contains_model(v) for v in value.values())
    elif isinstance(value, (list, tuple, set)):
        return any(contains_model(item) for item in value)


def save_request_file_to_disk(request_file) -> str:
    file_dir = os.path.join(tempfile.gettempdir(), str(uuid.uuid4()))
    os.makedirs(file_dir, exist_ok=True)
    
    file_path = os.path.join(file_dir, request_file.name)
    
    chunk_size = 64 * 1024  # 64kb
    with open(file_path, 'wb') as output_file:
        while True:
            chunk = request_file.file.read(chunk_size)
            if not chunk:
                break
            output_file.write(chunk)
    
    return file_path


def json_endpoint(function):
    @wraps(function)
    def wrap(request, *args, **kwargs):
        try:
            json_response = function(request, *args, **kwargs)
            
            if isinstance(json_response, Model):
                json_response = common.get_dict(json_response)
            elif isinstance(json_response, bool):
                json_response = {
                    "success": json_response
                }
            elif isinstance(json_response, list):
                r = []
                for jr in json_response:
                    if isinstance(jr, Model):
                        r.append(common.model_to_dict(jr))
                    else:
                        r.append(jr)
                json_response = r
            
            if json_response is None:
                json_response = {}
            
            response = JsonResponse(
                json_response,
                encoder=ErieIronJSONEncoder,
                status=200,
                safe=False
            )
            
            for (cookie_name, cookie_value) in common.ensure_list(json_response.get("cookies")):
                response.set_cookie(
                    cookie_name,
                    cookie_value,
                    max_age=COOKIE_AGE,
                    httponly=True,
                    secure=not settings.DEBUG
                )
            
            return response
        except Exception as e:
            logging.exception(str(e))
            
            try:
                return JsonResponse({
                    'error': str(e)
                }, status=400)
            except:
                return JsonResponse({
                    'error': 'unknown'
                }, status=400)
    
    return wrap


def parse_session_cookie(request, disabled=True):
    if disabled:
        return None
    
    id_token = request.COOKIES.get(COOKIE_SESSION_TOKEN)
    
    if common.is_empty(id_token):
        return None
    
    try:
        return parse_session_token(id_token)
    except jwt.ExpiredSignatureError:
        refresh_token = request.COOKIES.get(COOKIE_REFRESH_TOKEN)
        if common.is_empty(refresh_token):
            return None
        
        try:
            new_tokens = refresh_cognito_tokens(refresh_token)
            request.new_cognito_tokens = {
                'id_token': new_tokens.get('id_token'),
                'refresh_token': new_tokens.get('refresh_token', refresh_token)
            }
            return parse_session_token(request.new_cognito_tokens['id_token'])
        except Exception as e:
            logging.debug(e)
            return None
    except Exception as e:
        logging.exception(e)
        return None


def json_redirect_response(view, args=None, query_string=None, xtra_data=None):
    url = reverse(view, args=common.ensure_list(args))
    if query_string:
        url = f"{url}?{query_string}"
    
    if xtra_data is None:
        xtra_data = {}
    
    xtra_data['redirect_url'] = url
    
    return xtra_data


def get_current_user(request) -> models.Person:
    if not hasattr(request, "threadlocal_person_cache") or not request.threadlocal_person_cache:
        request.threadlocal_person_cache = _get_current_user_internal(request)
    
    return request.threadlocal_person_cache


def _get_current_user_internal(request) -> models.Person:
    user_data = parse_session_cookie(request)
    cognito_sub = common.get(user_data, 'sub')
    profile_photo_url = common.get(user_data, 'picture')
    try:
        person = models.Person.objects.get(cognito_sub=cognito_sub)
        return person
    except:
        return None


def send_response(request, template, context=None, validate=False, status_code=200, breadcrumbs=None):
    if context is None:
        context = {}
    
    if validate:
        validate_context(context)
    
    if common.parse_bool(common.get(request.POST, "no_header", False)):
        context["no_header"] = True
    
    allowed_back_dests = []
    from erieiron_config import urls
    # only allow back button for 'view_' methods
    for up in urls.urlpatterns:
        if common.default_str(up.lookup_str).startswith("webservice.views.view_"):
            allowed_back_dests.append(common.default_str(up.pattern.regex.pattern).replace("^", "").replace("\Z", "").split("/")[0])
    
    user_email: str = common.get(request, ["user_data", "email"], default_val="")
    
    context['breadcrumbs'] = [{"url": url, "label": label} for url, label in common.ensure_list(breadcrumbs)]
    context['allowed_back_dests'] = list(set(allowed_back_dests))
    context['user_data'] = common.get(request, "user_data")
    context['authenticated'] = context['user_data'] is not None
    context['is_internal_user'] = user_email.endswith("erieiron.ai") or user_email.endswith("collaya.com")
    context['pygments_css'] = HtmlFormatter().get_style_defs('.highlight')
    
    current_user = None
    try:
        current_user = get_current_user(request)
        context['current_user'] = current_user
        context['websocket_url'] = f"wss://{settings.CLIENT_MESSAGE_WEBSOCKET_ENDPOINT}?uid={current_user.pk}"
        context['consent_to_cookies'] = current_user.cookie_consent == 'accepted'
        context['current_user_id'] = current_user.id
        context['is_dev_view'] = current_user.has_role(Role.DEVELOPER) and settings.DEBUG
    except:
        pass
    
    response = render(request, template, context, status=status_code)
    csrf_token = csrf.get_token(request)
    response.set_cookie('csrftoken', csrf_token)
    
    return response


def redirect(redirect_url, cookies=None):
    response = HttpResponseRedirect(redirect_url)
    
    for (cookie_name, cookie_value) in common.ensure_list(cookies):
        if cookie_value is None:
            response.delete_cookie(cookie_name)
        else:
            response.set_cookie(
                cookie_name,
                cookie_value,
                max_age=COOKIE_AGE,
                httponly=True,
                secure=not settings.DEBUG
            )
    
    return response


def get_cognito_domain():
    """
    Get Cognito domain from agent_tools configuration.
    """
    config = agent_tools.get_cognito_config()
    return config['domain']


def get_cognito_client_id():
    """
    Get Cognito client ID from agent_tools configuration.
    """
    config = agent_tools.get_cognito_config()
    return config['clientId']


def get_cognito_user_pool_id():
    """
    Get Cognito user pool ID from agent_tools configuration.
    """
    config = agent_tools.get_cognito_config()
    return config['userPoolId']


def get_cognito_tokens_from_authcode(code):
    response = requests.post(
        get_cognito_domain() + "/oauth2/token",
        headers={
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        data={
            'grant_type': 'authorization_code',
            'client_id': get_cognito_client_id(),
            'code': code,
            'redirect_uri': build_absolute_uri("cognito_auth_callback")
        }
    )

    if response.status_code != 200:
        raise Exception(
            f"failed to fetch cognito tokens: {response.status_code}:  {response.text}"
        )

    return response.json()


def refresh_cognito_tokens(refresh_token):
    data = {
        'grant_type': 'refresh_token',
        'client_id': get_cognito_client_id(),
        'refresh_token': refresh_token
    }
    
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    response = requests.post(
        get_cognito_domain() + "/oauth2/token",
        headers=headers,
        data=data,
        auth=None
    )
    
    if response.status_code != 200:
        raise Exception(
            f"failed to refresh cognito tokens: {response.status_code}:  {response.text}"
        )
    
    return response.json()


def parse_session_token(id_token):
    if common.is_empty(id_token):
        return None

    if settings.DEBUG or runtime_config.RuntimeConfig.instance().get_bool("TEMP_USE_JWT_DECODER"):
        user_pool_id = get_cognito_user_pool_id()
        client_id = get_cognito_client_id()

        issuer = (
            f"https://cognito-idp.{settings.AWS_DEFAULT_REGION_NAME}.amazonaws.com/"
            f"{user_pool_id}"
        )

        jwks_client = PyJWKClient(f"{issuer}/.well-known/jwks.json")
        signing_key = jwks_client.get_signing_key_from_jwt(id_token).key
        return jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=issuer,
        )
    else:
        return jwt.decode(id_token, options={"verify_signature": False})


def rget_bool(request, key, default_val=False):
    if default_val is None:
        default_val = False
    val = rget(request, key)
    
    try:
        if val is None:
            return common.parse_bool(default_val)
        else:
            return common.parse_bool(val)
    except:
        return default_val


def rget_float(request, key, default_val=None):
    if default_val is None:
        default_val = 0.0
    
    val = rget(request, key)
    
    try:
        if val is None:
            return float(default_val)
        else:
            return float(val)
    except:
        return default_val


def rget_int(request, key, default_val=0):
    if default_val is None:
        default_val = 0
    
    val = rget(request, key)
    
    try:
        if val is None:
            return int(default_val)
        else:
            return round(float(val))
    except:
        return int(default_val)


def cget_bool(request, key, default_val=False):
    return common.parse_bool(
        cget(request, key, "true" if default_val else "false")
    )


def cget(request, key, default_val=None):
    val = request.COOKIES.get(key)
    if val is None:
        return default_val
    else:
        return val


def rget_list(request, key, delimeter=","):
    vals = request.POST.getlist(key)
    vals += request.GET.getlist(key)
    
    retvals = []
    for v in vals:
        retvals += common.safe_split(v, delimeter)
    
    return list(set(retvals))


def rget_json(request, key, default_val=None):
    s = rget(request, key)
    return json.loads(s) if s else default_val


def rget(request, key, default_val=None):
    if key in request.POST:
        return common.get(request.POST, key, default_val)
    elif key in request.GET:
        return common.get(request.GET, key, default_val)
    else:
        return default_val


def request_llm_async(
        person,
        description,
        messages,
        model,
        tag_entity=None,
        output_schema=None,
        reasoning_effort=None,
        verbosity=None,
        creativity=None,
        code_response=False,
        completion_view_url=None,
        completion_view_data=None
):
    """
    Helper function to publish an async LLM request to the PubSub queue.

    This allows views to request LLM processing without blocking the request thread.
    The LLM response will be sent to the frontend via WebSocket when complete.

    Args:
        person: Person object (current user)
        description: str - Description of the LLM request
        messages: list[LlmMessage] - Messages to send to the LLM
        model: LlmModel - The LLM model to use
        tag_entity: Optional entity to tag the LlmRequest with (Business, Initiative, Task, etc.)
        output_schema: Optional JSON schema for structured output
        reasoning_effort: LlmReasoningEffort - Level of reasoning effort
        verbosity: LlmVerbosity - Response verbosity level
        creativity: LlmCreativity - Creativity level
        code_response: bool - Whether response is code
        completion_view_url: Optional URL to POST results to (Option B pattern)
        completion_view_data: Optional dict of additional data to pass to completion view

    Returns:
        PubSubMessage object that was created

    Example:
        from erieiron_common.view_utils import request_llm_async
        from erieiron_common.llm_apis.llm_interface import LlmMessage
        from erieiron_common.enums import LlmModel

        @json_endpoint
        def my_view(request):
            person = get_current_user(request)
            messages = [LlmMessage.user("What is 2+2?")]

            request_llm_async(
                person=person,
                description="Simple math question",
                messages=messages,
                model=LlmModel.OPENAI_GPT_5_MINI,
                completion_view_url=reverse('my_completion_handler'),
                completion_view_data={"user_id": person.id}
            )

            return {"status": "processing"}
    """
    from erieiron_common.enums import PubSubMessageType, LlmReasoningEffort, LlmVerbosity, LlmCreativity
    from erieiron_common.message_queue.pubsub_manager import PubSubManager

    # Serialize messages to dict format for payload
    messages_data = []
    for msg in messages:
        msg_dict = {
            "message_type": msg.message_type.value,
            "text": msg.text
        }
        if hasattr(msg, 'file') and msg.file:
            # Note: file handling requires additional serialization
            # For now, we'll skip files in async requests
            pass
        messages_data.append(msg_dict)

    # Determine tag entity info
    tag_entity_type = None
    tag_entity_id = None
    if tag_entity:
        tag_entity_type = tag_entity.__class__.__name__
        tag_entity_id = str(tag_entity.id)

    # Build payload
    payload = {
        "person_id": str(person.id),
        "description": description,
        "messages": messages_data,
        "model": model.value,
        "tag_entity_type": tag_entity_type,
        "tag_entity_id": tag_entity_id,
        "code_response": code_response
    }

    # Add optional parameters if provided
    if output_schema:
        payload["output_schema"] = output_schema
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort.value
    if verbosity:
        payload["verbosity"] = verbosity.value
    if creativity:
        payload["creativity"] = creativity.value
    if completion_view_url:
        payload["completion_view_url"] = completion_view_url
    if completion_view_data:
        payload["completion_view_data"] = completion_view_data

    # Publish to PubSub
    message = PubSubManager.publish(
        message_type=PubSubMessageType.LLM_REQUEST,
        namespace_context=person.id,  # Use person ID as namespace for tracking
        payload=payload
    )

    common.log_info(f"Published async LLM request for {person.email}: {description}")

    return message
