import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from erieiron_common import opentofu_utils
from erieiron_common.enums import AwsEnv, InfrastructureStackType, TaskType
from erieiron_autonomous_agent.coding_agents import self_driving_coder_agent_tofu as tofu_agent


def test_validate_required_variables_identifies_missing():
    module_variables = {
        "Foo": opentofu_utils.OpenTofuVariable(
            name="Foo",
            required=True,
            default=None,
            type_expression=None,
            description=None,
            sensitive=False,
        ),
        "Bar": opentofu_utils.OpenTofuVariable(
            name="Bar",
            required=False,
            default="baz",
            type_expression=None,
            description=None,
            sensitive=False,
        ),
    }
    provided = {"Foo": "value"}

    missing = opentofu_utils.validate_required_variables(module_variables, provided)

    assert missing == []

    missing = opentofu_utils.validate_required_variables(module_variables, {})
    assert missing == ["Foo"]


def test_normalize_outputs_extracts_values():
    raw = {
        "no_wrap": "value",
        "wrapped": {"value": 42},
    }
    normalized = opentofu_utils.normalize_outputs(raw)
    assert normalized == {"no_wrap": "value", "wrapped": 42}


def test_write_tfvars_file_writes_json(tmp_path):
    workspace = Path(tmp_path)
    config = opentofu_utils.OpenTofuStackConfig(
        stack_type=InfrastructureStackType.APPLICATION,
        module=SimpleNamespace(workdir=workspace, entrypoint=workspace / "main.tf", exists=True, module_path=workspace),
        workspace_name="ws",
        workspace_dir=workspace,
    )

    data = {"Foo": "Bar"}
    path = opentofu_utils.write_tfvars_file(config, data)
    assert path.exists()
    assert json.loads(path.read_text()) == data


def test_summarize_plan_changes_counts_actions():
    plan_json = {
        "resource_changes": [
            {"change": {"actions": ["create"]}},
            {"change": {"actions": ["delete"]}},
            {"change": {"actions": ["create", "delete"]}},
            {"change": {"actions": ["update"]}},
            {"change": {"actions": ["no-op"]}},
        ]
    }
    summary = opentofu_utils.summarize_plan_changes(plan_json)
    assert summary == {"create": 2, "update": 1, "delete": 2, "replace": 1, "no-op": 1}


def test_build_tfvars_payload_includes_expected_fields(monkeypatch):
    stack_type = InfrastructureStackType.APPLICATION
    config = SimpleNamespace()
    config.opentofu_module_variables = {stack_type: {}}
    config.current_iteration = SimpleNamespace(planning_json={})
    config.aws_env = AwsEnv.DEV
    config.task_type = None

    business = SimpleNamespace(
        web_container_cpu=256,
        web_container_memory=512,
        web_desired_count=2,
        domain="example.com",
        route53_hosted_zone_id="Z123",
        domain_certificate_arn="arn:aws:acm:region:acct:certificate/123",
        required_credentials={},
    )

    def get_secrets_root_key(env):
        return "business/root"

    business.get_secrets_root_key = get_secrets_root_key
    config.business = business
    config.initiative = SimpleNamespace(domain="dev.example.com")
    config.self_driving_task = SimpleNamespace()

    foundation_stack = SimpleNamespace(stack_namespace_token="foundation-token")
    application_stack = SimpleNamespace(stack_namespace_token="application-token")

    monkeypatch.setattr(tofu_agent, "get_stacks", lambda _: (foundation_stack, application_stack))
    monkeypatch.setattr(tofu_agent.common, "get_ip_address", lambda: "198.51.100.1")

    shared_vpc = SimpleNamespace(
        vpc_id="vpc-123",
        cidr_block="10.0.0.0/16",
        public_subnet_ids=["subnet-public-a", "subnet-public-b"],
        private_subnet_ids=["subnet-private-a", "subnet-private-b"],
    )
    monkeypatch.setattr(tofu_agent.aws_utils, "get_shared_vpc", lambda: shared_vpc)
    monkeypatch.setattr(tofu_agent.aws_utils, "SHARED_RDS_SECURITY_GROUP_ID", "sg-123", raising=False)
    monkeypatch.setattr(tofu_agent, "get_admin_credentials", lambda env, key: {"AdminPassword": "secret"})

    previous_outputs = {
        "RdsMasterSecretArn": "arn:aws:secretsmanager:us-east-1:123:secret:rds",
        "RdsInstanceEndpoint": "db.example.com",
        "RdsInstancePort": "5432",
        "RdsInstanceDBName": "appdb",
    }

    container_env = {"ENV_SECRET": "arn:aws:secretsmanager:.."}
    lambda_datas = [{"s3_key_param": "LambdaAssetKey", "s3_key_name": "lambda.zip"}]

    payload = tofu_agent.build_tfvars_payload(
        config,
        stack_type=stack_type,
        container_env=container_env,
        web_container_image="123.dkr.ecr.amazonaws.com/app:latest",
        ecr_arn="arn:aws:ecr:region:acct:repository/app",
        lambda_datas=lambda_datas,
        previous_stack_outputs=previous_outputs,
    )

    assert payload["StackIdentifier"] == "application-token"
    assert payload["FoundationStackIdentifier"] == "foundation-token"
    assert payload["ClientIpForRemoteAccess"] == "198.51.100.1"
    assert payload["DeletePolicy"] == "Delete"
    assert payload["ECRRepositoryArn"] == "arn:aws:ecr:region:acct:repository/app"
    assert payload["WebContainerCpu"] == 256
    assert payload["WebContainerMemory"] == 512
    assert payload["WebDesiredCount"] == 2
    assert payload["WebContainerImage"] == "123.dkr.ecr.amazonaws.com/app:latest"
    assert payload["DomainName"] == "dev.example.com"
    assert payload["VpcId"] == "vpc-123"
    assert payload["PublicSubnet1Id"] == "subnet-public-a"
    assert payload["PrivateSubnet2Id"] == "subnet-private-b"
    assert payload["SecurityGroupId"] == "sg-123"
    assert payload["LambdaAssetKey"] == "lambda.zip"
    assert payload["RdsSecretArn"] == previous_outputs["RdsMasterSecretArn"]
    assert payload["DatabaseName"] == previous_outputs["RdsInstanceDBName"]
    assert payload["AdminPassword"] == "secret"
    assert payload["AWS_ACCOUNT_ID"] == tofu_agent.settings.AWS_ACCOUNT_ID


@pytest.mark.parametrize(
    "stack_logs,stack_errors,iteration_version,has_test,expected",
    [
        ({}, {}, 2, True, (False, "OpenTofu plan/apply logs were not captured. You may not declare goal complete.")),
        ({"app": {"error": "boom"}}, {"app": {"message": "boom"}}, 2, True, (False, "OpenTofu plan/apply reported errors. Resolve them before declaring success.")),
        ({"app": {}}, {}, 1, True, (False, "We have not written any code yet for this task")),
        ({"app": {}}, {}, 2, False, (False, "this task does not yet have an automated test.  Need to write an automated test and make it pass before allowing goal achieved")),
        ({"app": {}}, {}, 2, True, (True, "we've written code and it deployed successfully")),
    ],
)
def test_compute_goal_achievement_gate(stack_logs, stack_errors, iteration_version, has_test, expected):
    result = tofu_agent.compute_goal_achievement_gate(
        task_type=TaskType.CODING_APPLICATION,
        iteration_version=iteration_version,
        has_test=has_test,
        stack_logs=stack_logs,
        stack_errors=stack_errors,
    )
    assert result == expected
