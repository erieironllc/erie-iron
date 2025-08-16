import json
import secrets
import string

from erieiron_autonomous_agent.coding_agents.self_driving_coder_config import AgentBlocked, SelfDriverConfig
from erieiron_autonomous_agent.models import Business, Task
from erieiron_common import aws_utils, common
from erieiron_common.enums import CredentialService, AwsEnv

CREDENTIALSERVICE_TO_CREDENTIALDEF = {
    CredentialService.RDS: {
        "secret_arn_env_var": "RDS_CREDS_ARN",
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
            },
            {
                "key": "host",
                "type": "string",
                "required": False,
                "description": "RDS instance endpoint"
            },
            {
                "key": "port",
                "type": "int",
                "required": False,
                "description": "RDS instance port"
            },
            {
                "key": "database",
                "type": "string",
                "required": False,
                "description": "Database name"
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
        config:SelfDriverConfig,
        aws_env: AwsEnv,
        credential_service_name: str,
        cred_def: dict
) -> str:
    business = config.business
    task = config.task
    
    if not CredentialService.valid(common.default_str(credential_service_name).upper()):
        raise AgentBlocked(f"""Blocked by unsupported credential service: {credential_service_name}

Need a human to set this up

Business:  {business.name} ({business.id})
Env:  {aws_env}

Secret Def:
{json.dumps(cred_def, indent=4)}
""")
    
    aws_secret_key, secret_dict = get_credential_secret(
        business,
        aws_env,
        task,
        credential_service_name
    )
    
    credential_service = CredentialService.valid_or(credential_service_name)
    missing_secret_vals = validate_secret(secret_dict, CREDENTIALSERVICE_TO_CREDENTIALDEF.get(credential_service, cred_def))
    if not missing_secret_vals:
        return aws_utils.get_secret_arn(aws_secret_key)
    
    if credential_service == CredentialService.RDS:
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
    
    secret_arn = aws_utils.put_secret(aws_secret_key, secret_dict)
    return secret_arn


def get_aws_role_name(
        config:SelfDriverConfig,
        env: AwsEnv
):
    business = config.business
    task = config.task
    
    role_name = f"{business.service_token}-{env}"
    if AwsEnv.PRODUCTION.DEV.eq(env):
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
        aws_env: AwsEnv,
        task: Task,
        credential_service_name: str
):
    prefix = business.get_secrets_root_key(aws_env)
    aws_secret_key = f"{prefix}/{credential_service_name}"
    
    aws_secret_key = aws_utils.sanitize_aws_name(aws_secret_key, 512)
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


def create_password(length):
    password_alphabet = string.ascii_letters + string.digits + string.punctuation
    return ''.join(secrets.choice(password_alphabet) for _ in range(length))
