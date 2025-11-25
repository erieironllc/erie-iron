import json
import secrets
import string

from erieiron_autonomous_agent.coding_agents.coding_agent_config import CodingAgentConfig
from erieiron_common import aws_utils, common
from erieiron_common.enums import CredentialService, EnvironmentType, CredentialServiceProvisioning

DISALLOWED_RDS_PASSWORD_CHARS = set('/@" ')
ALLOWED_RDS_SPECIALS = ''.join(ch for ch in string.punctuation if ch not in DISALLOWED_RDS_PASSWORD_CHARS)

CREDENTIALSERVICE_TO_CREDENTIALDEF = {
    CredentialService.RDS: {
        "secret_arn_env_var": "RDS_SECRET_ARN",
        "secret_arn_cfn_parameter": "RdsSecretArn",
        "provisioning": CredentialServiceProvisioning.STACK_GENERATED,
        "schema": [
            {
                "key": "username",
                "type": "string",
                "required": True,
                "description": "Database username for the application"
            },
            {
                "key": "password",
                "type": "string",
                "required": True,
                "description": "Database password for the application"
            }
        ]
    },
    CredentialService.COGNITO: {
        "secret_arn_env_var": "COGNITO_SECRET_ARN",
        "secret_arn_cfn_parameter": "CognitoSecretArn",
        "provisioning": CredentialServiceProvisioning.STACK_GENERATED,
        "schema": [
            {"key": "user_pool_id", "type": "string", "required": True, "description": "Cognito User Pool ID"},
            {"key": "client_id", "type": "string", "required": True, "description": "Cognito App Client ID"},
            {"key": "client_secret", "type": "string", "required": False, "description": "Cognito App Client Secret"}
        ]
    },
    CredentialService.STRIPE: {
        "secret_arn_env_var": "STRIPE_API_KEY_SECRET_ARN",
        "secret_arn_cfn_parameter": "StripeApiKeySecretArn",
        "provisioning": CredentialServiceProvisioning.USER_SUPPLIED,
        "schema": [
            {"key": "api_key", "type": "string", "required": True, "description": "Stripe API secret key"},
            {"key": "publishable_key", "type": "string", "required": False, "description": "Stripe publishable key"},
            {"key": "webhook_secret", "type": "string", "required": False, "description": "Stripe webhook signing secret"}
        ]
    },
    CredentialService.HCAPTCHA: {
        "secret_arn_env_var": "HCAPTCHA_SECRET_ARN",
        "secret_arn_cfn_parameter": "HcaptchaSecretArn",
        "provisioning": CredentialServiceProvisioning.USER_SUPPLIED,
        "schema": [
            {"key": "secret_key", "type": "string", "required": True, "description": "hCaptcha secret key"},
            {"key": "site_key", "type": "string", "required": False, "description": "hCaptcha site key"}
        ]
    },
    CredentialService.ONESIGNAL: {
        "secret_arn_env_var": "ONESIGNAL_SECRET_ARN",
        "secret_arn_cfn_parameter": "OnesignalSecretArn",
        "provisioning": CredentialServiceProvisioning.USER_SUPPLIED,
        "schema": [
            {"key": "app_id", "type": "string", "required": True, "description": "OneSignal App ID"},
            {"key": "api_key", "type": "string", "required": True, "description": "OneSignal REST API Key"}
        ]
    },
    CredentialService.OAUTH_APPLE: {
        "secret_arn_env_var": "OAUTH_APPLE_SECRET_ARN",
        "secret_arn_cfn_parameter": "OauthAppleSecretArn",
        "provisioning": CredentialServiceProvisioning.USER_SUPPLIED,
        "schema": [
            {"key": "client_id", "type": "string", "required": True, "description": "Apple Services ID"},
            {"key": "team_id", "type": "string", "required": True, "description": "Apple Team ID"},
            {"key": "key_id", "type": "string", "required": True, "description": "Apple Key ID"},
            {"key": "private_key", "type": "string", "required": True, "description": "Apple Private Key (PEM format)"}
        ]
    },
    CredentialService.FIREBASE_FCM: {
        "secret_arn_env_var": "FIREBASE_FCM_SECRET_ARN",
        "secret_arn_cfn_parameter": "FirebaseFcmSecretArn",
        "provisioning": CredentialServiceProvisioning.USER_SUPPLIED,
        "schema": [
            {"key": "server_key", "type": "string", "required": True, "description": "Firebase Cloud Messaging server key"},
            {"key": "sender_id", "type": "string", "required": False, "description": "Firebase sender ID"}
        ]
    },
    CredentialService.OAUTH_GITHUB: {
        "secret_arn_env_var": "OAUTH_GITHUB_SECRET_ARN",
        "secret_arn_cfn_parameter": "OauthGithubSecretArn",
        "provisioning": CredentialServiceProvisioning.USER_SUPPLIED,
        "schema": [
            {"key": "client_id", "type": "string", "required": True, "description": "GitHub OAuth App Client ID"},
            {"key": "client_secret", "type": "string", "required": True, "description": "GitHub OAuth App Client Secret"}
        ]
    },
    CredentialService.OAUTH_GOOGLE: {
        "secret_arn_env_var": "OAUTH_GOOGLE_SECRET_ARN",
        "secret_arn_cfn_parameter": "OauthGoogleSecretArn",
        "provisioning": CredentialServiceProvisioning.USER_SUPPLIED,
        "schema": [
            {"key": "client_id", "type": "string", "required": True, "description": "Google OAuth Client ID"},
            {"key": "client_secret", "type": "string", "required": True, "description": "Google OAuth Client Secret"}
        ]
    },
    CredentialService.COINBASE_COMMERCE: {
        "secret_arn_env_var": "COINBASE_COMMERCE_SECRET_ARN",
        "secret_arn_cfn_parameter": "CoinbaseCommerceSecretArn",
        "provisioning": CredentialServiceProvisioning.USER_SUPPLIED,
        "schema": [
            {"key": "api_key", "type": "string", "required": True, "description": "Coinbase Commerce API Key"},
            {"key": "webhook_secret", "type": "string", "required": False, "description": "Coinbase Commerce webhook shared secret"}
        ]
    },
    CredentialService.DJANGO: {
        "secret_arn_env_var": "DJANGO_SECRET_KEY_ARN",
        "secret_arn_cfn_parameter": "DjangoSecretKeyArn",
        "provisioning": CredentialServiceProvisioning.USER_SUPPLIED,
        "schema": [
            {"key": "secret_key", "type": "string", "required": True, "description": "Django SECRET_KEY for cryptographic signing"}
        ]
    },
    CredentialService.LLM: {
        "secret_arn_env_var": "LLM_API_KEYS_SECRET_ARN",
        "secret_arn_cfn_parameter": "LlmApiKeysSecretArn",
        "provisioning": CredentialServiceProvisioning.USER_SUPPLIED,
        "schema": [
            {"key": "anthropic_api_key", "type": "string", "required": False, "description": "Anthropic Claude API key"},
            {"key": "openai_api_key", "type": "string", "required": False, "description": "OpenAI API key"},
            {"key": "google_api_key", "type": "string", "required": False, "description": "Google Gemini API key"},
            {"key": "deepseek_api_key", "type": "string", "required": False, "description": "DeepSeek API key"}
        ]
    }
}


def get_desc(credential_service: CredentialService) -> str:
    return CREDENTIALSERVICE_TO_CREDENTIALDEF[
        CredentialService(credential_service)
    ].get(
        "secret_arn_env_var"
    ).replace(
        "_SECRET_ARN", ""
    ).replace(
        "_", " "
    ).title()


def get_existing_service_names_desc() -> str:
    return "; ".join(
        f"{cred_service} ({get_desc(cred_service)})"
        for cred_service in CredentialService
    )


def get_existing_service_schema_desc() -> str:
    return json.dumps(CREDENTIALSERVICE_TO_CREDENTIALDEF, indent=4)


def get_aws_role_name(
        config: CodingAgentConfig,
        env: EnvironmentType
):
    business = config.business
    task = config.task
    
    role_name = f"{business.service_token}-{env}"
    if EnvironmentType.PRODUCTION.DEV.eq(env):
        chars_avail = 63 - len(role_name)
        if chars_avail > 5:
            task_suffix = f"-{task.id[0:chars_avail]}"
        else:
            # let the sanitizer figure it out
            task_suffix = f"-{task.id}"
        role_name += task_suffix
    
    role_name = aws_utils.sanitize_aws_name(role_name, 64)
    
    return role_name


def validate_secret(secret_dict, credential_def):
    missing_vals = []
    for schema_item in common.ensure_list(credential_def.get("schema")):
        schema_key = schema_item.get("key")
        secret_val = secret_dict.get(schema_key)
        if not secret_val and common.parse_bool(schema_item.get("required")):
            missing_vals.append(schema_item)
    
    return missing_vals


def random_string(length=16):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def create_password(length: int) -> str:
    """Generate a strong password compatible with AWS RDS constraints.

    Rules enforced:
    - Only printable ASCII excluding '/', '@', '"', and space.
    - At least one lowercase, uppercase, digit, and special character.
    - Length clamped to [12, 128].
    """
    if length < 12:
        length = 12
    if length > 128:
        length = 128
    
    lowers = string.ascii_lowercase
    uppers = string.ascii_uppercase
    digits = string.digits
    specials = ALLOWED_RDS_SPECIALS
    
    # Fallback in the unlikely case specials is empty
    if not specials:
        specials = '!#$%^&*()-_=+[]{}:,;.?\\|~'  # still excludes '/', '@', '"', and space
    
    all_chars = lowers + uppers + digits + specials
    
    rng = secrets.SystemRandom()
    
    while True:
        pwd_chars = [
            rng.choice(lowers),
            rng.choice(uppers),
            rng.choice(digits),
            rng.choice(specials),
        ]
        pwd_chars += [rng.choice(all_chars) for _ in range(length - len(pwd_chars))]
        rng.shuffle(pwd_chars)
        candidate = ''.join(pwd_chars)
        if is_valid_rds_password(candidate):
            return candidate


def is_valid_rds_password(pw: str) -> bool:
    if not isinstance(pw, str):
        return False
    # RDS allows 8-128 length
    if not (8 <= len(pw) <= 128):
        return False
    # Only printable ASCII 33..126 and none of the disallowed
    for ch in pw:
        o = ord(ch)
        if o < 33 or o > 126 or ch in DISALLOWED_RDS_PASSWORD_CHARS:
            return False
    # Basic complexity
    has_lower = any(c.islower() for c in pw)
    has_upper = any(c.isupper() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    has_special = any(c in ALLOWED_RDS_SPECIALS for c in pw)
    return has_lower and has_upper and has_digit and has_special
