import json
import secrets
import string

import settings
from erieiron_autonomous_agent.coding_agents.self_driving_coder_config import SelfDriverConfig
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import AgentBlocked
from erieiron_autonomous_agent.models import Business, Task
from erieiron_common import aws_utils, common
from erieiron_common.enums import CredentialService, EnvironmentType

DISALLOWED_RDS_PASSWORD_CHARS = set('/@" ')
ALLOWED_RDS_SPECIALS = ''.join(ch for ch in string.punctuation if ch not in DISALLOWED_RDS_PASSWORD_CHARS)

CREDENTIALSERVICE_TO_CREDENTIALDEF = {
    CredentialService.RDS: {
        "secret_arn_env_var": "RDS_SECRET_ARN",
        "secret_arn_cfn_parameter": "RdsSecretArn",
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
    }
    
}


def get_existing_service_names_desc() -> str:
    return "; ".join(
        f"{cred_service} ({cred_service.get_desc()})"
        for cred_service in CredentialService
    )


def get_existing_service_schema_desc() -> str:
    return json.dumps(CREDENTIALSERVICE_TO_CREDENTIALDEF, indent=4)


def manage_credentials(
        business:Business,
        task:Task,
        env_type: EnvironmentType,
        credential_service_name: str,
        cred_def: dict
) -> str:
    secret_arn_env_var = cred_def.get("secret_arn_env_var")
    if secret_arn_env_var == "LLM_API_KEYS_SECRET_ARN":
        return settings.LLM_API_KEYS_SECRET_ARN
    
    if secret_arn_env_var == "STRIPE_WEBHOOK_SECRET_ARN":
        return settings.STRIPE_WEBHOOK_SECRET_ARN
    
    credential_service = CredentialService.valid_or(common.default_str(credential_service_name).upper())
    if not credential_service:
        raise AgentBlocked(f"""Blocked by unsupported credential service: {credential_service_name}

Need a human to set this up

Business:  {business.name} ({business.id})
Env:  {env_type}

Secret Def:
{json.dumps(cred_def, indent=4)}
""")
    
    aws_secret_key, secret_dict = get_credential_secret(
        business,
        env_type,
        task,
        credential_service_name
    )
    
    missing_secret_vals = validate_secret(
        secret_dict,
        CREDENTIALSERVICE_TO_CREDENTIALDEF.get(credential_service, cred_def)
    )
    
    if not missing_secret_vals:
        return aws_utils.get_secret_arn(aws_secret_key)
    
    if credential_service == CredentialService.RDS:
        # If a password exists but violates RDS rules, replace it
        existing_pwd = secret_dict.get('password')
        if existing_pwd and not is_valid_rds_password(existing_pwd):
            secret_dict['password'] = create_password(24)
        for schema_entity in missing_secret_vals:
            prop_name = schema_entity.get('key')
            if prop_name == "username":
                val = "postgres"
            elif prop_name == "password":
                val = create_password(24)
            elif prop_name == "host":
                val = ""
            elif prop_name == "port":
                val = 5432
            elif prop_name == "database":
                val = ""
            else:
                raise AgentBlocked(f"invalid prop name {prop_name}")
            
            secret_dict[prop_name] = val
    
    secret_arn = aws_utils.put_secret(
        aws_secret_key,
        secret_dict
    )
    
    return secret_arn


def get_aws_role_name(
        config: SelfDriverConfig,
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


def get_credential_secret(
        business: Business,
        env_type: EnvironmentType,
        task: Task,
        credential_service_name: str
):
    aws_secret_key = [
        business.get_secrets_root_key(env_type)
    ]
    if env_type not in [EnvironmentType.PRODUCTION]:
        aws_secret_key.append(task.id)
    aws_secret_key.append(credential_service_name)
    
    aws_secret_key = aws_utils.sanitize_aws_name("/".join(aws_secret_key), 512)
    
    try:
        secret_dict = aws_utils.get_secret(aws_secret_key)
    except:
        secret_dict = {}
    
    return aws_secret_key, secret_dict


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
