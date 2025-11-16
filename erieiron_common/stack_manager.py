"""Utilities for working with OpenTofu (Terraform) configurations."""
from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import tempfile
import textwrap
import time
from datetime import timedelta, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import hcl2

import settings
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import BadPlan
from erieiron_autonomous_agent.models import InfrastructureStack
from erieiron_common import aws_utils
from erieiron_common import common, opentofu_log_utils
from erieiron_common.date_utils import to_utc
from erieiron_common.enums import InfrastructureStackType, EnvironmentType
from erieiron_common.opentofu_helpers import OpenTofuVariable, OpenTofuCommandResult, OpenTofuCommandError, OpenTofuException
from erieiron_common.opentofu_log_utils import OpenTofuRunResult


class StackManager:
    def __init__(
            self,
            stack: InfrastructureStack,
            container_env: dict = None,
            sandbox_root: Path = None
    ):
        logging.info(f"Initiatize Stack Mangage for {stack.stack_namespace_token} ({stack.stack_type})")
        
        self.stack = stack
        self.cloud_account = self.stack.get_cloud_account()
        self.start_time = common.get_now()
        
        self.container_env = container_env or {}
        
        self.stack_type = InfrastructureStackType(self.stack.stack_type)
        self.sandbox_root = sandbox_root or Path(tempfile.mkdtemp(prefix=f"opentofu_temp_root_{self.stack_type}".lower()))
        
        self.workspace_dir = common.mkdirs(self.sandbox_root / "opentofu" / "workspaces" / self.get_workspace_name())
        self.module_dir = common.mkdirs(self.sandbox_root / "opentofu" / "swizzled_modules" / self.stack_type.value.lower())
        
        self.swizzled_module_file = self.get_swizzled_module_file()
        
        self.full_env = self.tf_env = self.build_opentofu_env()
        
        self.stage = "init"
        self.run_results: list[OpenTofuRunResult] = []
        
        self.plan_output_path = self.workspace_dir / "current.plan"
        self.init_workspace()
    
    def get_swizzled_module_file(self):
        stack_config_source_file = self.sandbox_root / self.stack_type.get_opentofu_config()
        
        if stack_config_source_file.exists():
            self.stack.stack_configuration = stack_config_source_file.read_text()
            self.stack.save()
        
        swizzled_contents = self.stack.stack_configuration.replace(
            "ERIE_IRON_RETAIN_RESOURCES",
            "true" if EnvironmentType.PRODUCTION.eq(self.stack.env_type) else "false"
        )
        
        swizzled_path = self.module_dir / "stack.tf"
        
        with swizzled_path.open("w", encoding="utf-8") as f:
            f.write(swizzled_contents)
        
        return swizzled_path
    
    def _sanitize_business_name(self, business_name: str) -> str:
        """
        Sanitize business name for S3 bucket naming requirements.
        S3 bucket names must be lowercase, only letters/numbers/hyphens, start/end with letter/number.
        """
        if not business_name:
            raise ValueError("Business name cannot be empty")
        
        # Convert to lowercase and remove invalid characters
        sanitized = business_name.lower()
        sanitized = re.sub(r'[^a-z0-9-]', '', sanitized)
        # Remove leading/trailing hyphens and collapse multiple hyphens
        sanitized = re.sub(r'^-+|-+$', '', sanitized)
        sanitized = re.sub(r'-+', '-', sanitized)
        
        # Validate sanitized name
        if not sanitized or len(sanitized) < 3:
            raise ValueError(f"Business name '{business_name}' cannot be sanitized to valid S3 bucket format")
        
        return sanitized
    
    def _get_terraform_state_bucket_name(self) -> str:
        """
        Generate the S3 bucket name for OpenTofu state storage.
        Follows the same pattern as apply_target_account_bootstrap.sh:
        erieiron-opentofu-state-{sanitized_business_name}-{account_id}
        """
        business_name = self.stack.business.name
        sanitized_business_name = self._sanitize_business_name(business_name)
        
        # Get account ID from cloud_account if available, otherwise use current account context
        if self.cloud_account.account_identifier:
            account_id = self.cloud_account.account_identifier
        else:
            # For development stacks or when cloud_account is not set,
            # the bucket should be in the same account where the operation is running
            # This will be determined by the AWS credentials context at runtime
            try:
                sts = self.cloud_account.get_service_client('sts')
                account_id = sts.get_caller_identity()['Account']
            except Exception as e:
                logging.error(f"Failed to get current AWS account ID: {e}")
                raise ValueError("Could not determine target AWS account ID for state bucket")
        
        bucket_name = f"erieiron-opentofu-state-{sanitized_business_name}-{account_id}"
        
        # Validate bucket name length (S3 limit is 63 characters)
        if len(bucket_name) > 63:
            raise ValueError(
                f"Generated bucket name '{bucket_name}' exceeds 63 character limit ({len(bucket_name)} chars). "
                f"Consider using a shorter business name."
            )
        
        logging.debug(f"Generated state bucket name: {bucket_name} (business: {business_name}, account: {account_id})")
        return bucket_name
    
    def record(self, stage: str, result: OpenTofuCommandResult) -> None:
        self.stage = stage
        self.run_results.append(
            opentofu_log_utils.OpenTofuRunResult(
                stage=stage,
                command=result.command,
                returncode=result.returncode,
                started_at=result.started_at,
                completed_at=result.completed_at,
                stdout=result.stdout,
                stderr=result.stderr,
                extra=result.extra,
            )
        )
    
    @staticmethod
    def validate_required_variables(
            module_variables: Mapping[str, OpenTofuVariable],
            provided_values: Mapping[str, Any],
    ) -> list[str]:
        missing: list[str] = []
        for name, variable in module_variables.items():
            if not variable.required:
                continue
            if name in provided_values:
                continue
            missing.append(name)
        return missing
    
    def normalize_outputs(self, raw_outputs: Mapping[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in raw_outputs.items():
            if isinstance(value, Mapping) and "value" in value:
                normalized[key] = value.get("value")
            else:
                normalized[key] = value
        return normalized
    
    def run_tofu_command(
            self,
            stage: str,
            args: Sequence[str],
            *,
            input_data: str | None = None,
            timeout: int | None = None,
    ) -> OpenTofuCommandResult:
        self.stage = stage
        workdir = str(self.workspace_dir)
        command = [settings.TOFU_BIN, f"-chdir={self.module_dir}", *common.strings(args)]
        started_at = common.get_now()
        logging.info(f"{stage}: {command}")
        try:
            completed_process = subprocess.run(
                common.strings(command),
                cwd=workdir,
                input=input_data,
                text=True,
                capture_output=True,
                env=self.full_env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            completed_at = common.get_now()
            result = OpenTofuCommandResult(
                command=command,
                cwd=workdir,
                started_at=started_at,
                completed_at=completed_at,
                returncode=-1,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
            )
            raise OpenTofuCommandError(
                f"OpenTofu command timed out after {timeout}s: {' '.join(command)}",
                result,
            ) from exc
        
        completed_at = common.get_now()
        result = OpenTofuCommandResult(
            command=command,
            cwd=workdir,
            started_at=started_at,
            completed_at=completed_at,
            returncode=completed_process.returncode,
            stdout=completed_process.stdout or "",
            stderr=completed_process.stderr or "",
        )
        
        if result.returncode != 0:
            # Allow return codes 2 and 3 for plan stage (indicate "changes pending" or "changes present")
            if result.returncode in (2, 3):
                logging.info(f"OpenTofu plan completed with changes (exit code {result.returncode}). Treating as success.")
            else:
                logging.error(completed_process.stderr)
                raise OpenTofuCommandError(
                    f"OpenTofu command failed with exit code {result.returncode}",
                    result,
                )
        
        logging.debug("OpenTofu command completed", extra=result.loggable_dict())
        self.record(stage, result)
        return result
    
    def init_workspace(self) -> OpenTofuCommandResult:
        un_swizzled_module_file = self.sandbox_root / self.stack_type.get_opentofu_config()
        lock_file = self.swizzled_module_file.parent / ".terraform.lock.hcl"
        
        if not lock_file.exists():
            use_upgrade = True
            reason = ".terraform.lock.hcl does not exist"
        elif common.is_file1_newer(un_swizzled_module_file, lock_file):
            use_upgrade = True
            reason = "The module configuration file is newer than the lock file"
        else:
            use_upgrade = False
            reason = None
        
        if use_upgrade:
            logging.info("OpenTofu init: using -upgrade (%s)", reason)
        
        args = ["init", "-input=false", "-no-color"]
        if use_upgrade:
            args.append("-upgrade")
        
        key_value = f"{self.stack.stack_namespace_token}/stack.tfstate"
        
        # Generate bucket name following same pattern as apply_target_account_bootstrap.sh
        terraform_state_bucket = self._get_terraform_state_bucket_name()
        
        storage_key_args = [
            f"-backend-config=bucket={terraform_state_bucket}",
            f"-backend-config=key={key_value}",
            f"-backend-config=region={EnvironmentType(self.stack.env_type).get_aws_region()}"
        ]
        args += storage_key_args
        
        last_exception = None
        
        for attempt in range(3):
            try:
                return self.run_tofu_command("init", args)
            except OpenTofuCommandError as e:
                last_exception = e
                time.sleep(10 * (attempt + 1))
                try:
                    self.run_tofu_command("init", ["init", "-reconfigure", "-input=false", "-no-color"] + storage_key_args)
                except OpenTofuCommandError as e2:
                    logging.exception(e2)
                    raise e2
        
        raise last_exception
    
    def plan(
            self,
            *,
            timeout: int | None = None,
            destroy: bool = False,
            refresh: bool = True,
            json_output: bool = True
    ) -> OpenTofuCommandResult:
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        try:
            args = ["plan", "-input=false", "-no-color"]
            args.extend(["-out", str(self.plan_output_path)])
            
            if destroy:
                args.append("-destroy")
            if not refresh:
                args.append("-refresh=false")
            
            args.extend(["-var-file", self.get_tfvars_file()])
            
            result = self.run_tofu_command(
                "plan",
                args,
                timeout=timeout
            )
            
            if json_output:
                show_result = self.run_tofu_command(
                    "show",
                    ["show", "-json", str(self.plan_output_path)],
                    timeout=timeout,
                )
                try:
                    plan_json = json.loads(show_result.stdout or "{}")
                except json.JSONDecodeError as exc:
                    raise OpenTofuCommandError(
                        "Failed to decode OpenTofu plan JSON",
                        show_result,
                    ) from exc
                change_summary = self.summarize_plan_changes(plan_json)
                result.extra["plan_path"] = str(self.plan_output_path)
                result.extra["plan_json"] = plan_json
                result.extra["plan_change_summary"] = change_summary
                result.extra["show_result"] = show_result.loggable_dict()
            return result
        finally:
            if temp_dir:
                temp_dir.cleanup()
    
    def apply(
            self,
            *,
            timeout: int | None = None,
            auto_approve: bool = True,
            retries: int = 0,
            retry_backoff_seconds: float = 5.0,
    ) -> OpenTofuCommandResult:
        args = ["apply", "-input=false", "-no-color"]
        if auto_approve:
            args.append("-auto-approve")
        args.append(str(self.plan_output_path))
        
        attempt = 0
        exc: OpenTofuCommandError | None = None
        known_duplicate_msgs = [
            "InvalidPermission.Duplicate",
            "InvalidChangeBatch",
            "AlreadyExists"
        ]
        msg = ""
        while attempt <= max(retries, 0):
            try:
                result = self.run_tofu_command(
                    "apply",
                    args,
                    timeout=timeout
                )
                
                # After successful apply, collect outputs reliably
                result.extra["outputs"] = self.get_outputs(timeout=timeout)
                
                return result
            except OpenTofuCommandError as error:
                # Enhanced error handling for "already exists"/duplicate conditions
                stderr = error.result.stderr or ""
                duplicate_found = False
                for msg in known_duplicate_msgs:
                    if msg in stderr:
                        duplicate_found = True
                        break
                if duplicate_found:
                    logging.debug(
                        "OpenTofu apply encountered known duplicate/exists error; treating as successful",
                        extra={
                            "attempt": attempt + 1,
                            "error_message": stderr,
                            "command": " ".join(shlex.quote(part) for part in args),
                            "workspace": self.get_workspace_name(),
                        },
                    )
                    # Synthesize a successful OpenTofuCommandResult, mark the stage, and return
                    synthetic_result = OpenTofuCommandResult(
                        command=[settings.TOFU_BIN, *args],
                        cwd=self.workspace_dir,
                        started_at=error.result.started_at,
                        completed_at=error.result.completed_at,
                        returncode=0,
                        stdout=error.result.stdout or "",
                        stderr="",
                        extra=dict(error_handled="duplicate/exists", summary="Handled known duplicate/exists error in apply"),
                    )
                    self.record("apply", synthetic_result)
                    synthetic_result.extra["handled_duplicate_message"] = True
                    synthetic_result.extra["handled_duplicate_detail"] = f"Handled known duplicate/exists error: {msg} found in stderr"
                    
                    # Reliably collect outputs even for synthetic results
                    synthetic_result.extra["outputs"] = self.get_outputs(timeout=timeout)
                    
                    return synthetic_result
                exc = error
                attempt += 1
                if attempt > max(retries, 0):
                    break
                logging.warning(
                    "OpenTofu apply failed; retrying",
                    extra={
                        "attempt": attempt,
                        "retries": retries,
                        "command": " ".join(shlex.quote(part) for part in args),
                        "workspace": self.get_workspace_name(),
                    },
                )
                time.sleep(retry_backoff_seconds * attempt)
        if exc:
            raise exc
        raise OpenTofuCommandError(
            "OpenTofu apply failed without raising an exception",
            OpenTofuCommandResult(
                command=[settings.TOFU_BIN, *args],
                cwd=self.workspace_dir,
                started_at=common.get_now(),
                completed_at=common.get_now(),
                returncode=-1,
                stdout="",
                stderr="apply aborted without execution",
            ),
        )
    
    def get_outputs(
            self,
            *,
            timeout: int | None = None,
    ) -> dict[str, Any]:
        refresh_args = ["refresh", "-no-color", "-input=false"]
        refresh_args.extend(["-var-file", self.get_tfvars_file()])
        
        self.run_tofu_command("refresh", refresh_args)
        
        result = self.run_tofu_command("outputs", ["output", "-json"], timeout=timeout)
        try:
            outputs = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise OpenTofuCommandError("Failed to decode OpenTofu outputs", result) from exc
        
        normalized: dict[str, Any] = {}
        for key, value in outputs.items():
            if isinstance(value, Mapping) and "value" in value:
                normalized[key] = value.get("value")
            else:
                normalized[key] = value
        
        return normalized
    
    def summarize_plan_changes(self, plan_json: Mapping[str, Any]) -> Mapping[str, int]:
        resource_changes = plan_json.get("resource_changes")
        summary: dict[str, int] = {"create": 0, "update": 0, "delete": 0, "replace": 0, "no-op": 0}
        if not isinstance(resource_changes, list):
            return summary
        
        for change in resource_changes:
            if not isinstance(change, Mapping):
                continue
            change_actions = change.get("change", {}).get("actions", [])
            if not isinstance(change_actions, list):
                continue
            actions = tuple(action.lower() for action in change_actions)
            if actions == ("create", "delete") or actions == ("delete", "create"):
                summary["replace"] += 1
            for action in actions:
                if action in summary:
                    summary[action] += 1
        return summary
    
    def get_workspace_name(self):
        
        base = aws_utils.sanitize_aws_name(self.stack.stack_namespace_token, max_length=40)
        suffix = aws_utils.sanitize_aws_name(self.stack.stack_type.lower(), max_length=20)
        return aws_utils.sanitize_aws_name(f"{base}-{suffix}", max_length=63)
    
    def get_state_file(self):
        return str(self.workspace_dir / "terraform.tfstate"),
    
    def get_state_locator(self):
        return f"opentofu://workspace/{self.get_workspace_name()}"
    
    def get_stack_variables(self) -> dict[str, OpenTofuVariable]:
        tf_path = self.swizzled_module_file
        
        try:
            with tf_path.open("r", encoding="utf-8") as tf_file:
                parsed_terraform = hcl2.load(tf_file)  # type: ignore[arg-type]
        except Exception as exc:
            logging.exception(exc)
            raise OpenTofuException(
                f"Failed to parse Terraform file '{tf_path}'"
            ) from exc
        
        variables: dict[str, OpenTofuVariable] = {}
        for variable_block in parsed_terraform.get("variable", []) or []:
            if not isinstance(variable_block, dict):
                continue
            for var_name, attributes in variable_block.items():
                if not isinstance(var_name, str):
                    continue
                attrs = attributes if isinstance(attributes, dict) else {}
                variables[var_name] = OpenTofuVariable(
                    name=var_name,
                    required="default" not in attrs,
                    default=attrs.get("default"),
                    type_expression=attrs.get("type"),
                    description=attrs.get("description"),
                    sensitive=bool(attrs.get("sensitive")),
                    source_file=str(tf_path),
                )
        
        return variables
    
    def get_tfvars_file(self) -> Path:
        tfvars_path = self.workspace_dir / f"{self.stack.stack_type.lower()}.auto.tfvars.json"
        tfvars_path.parent.mkdir(parents=True, exist_ok=True)
        common.write_json(tfvars_path, self.stack.stack_vars or {})
        return tfvars_path
    
    def build_opentofu_env(self) -> dict[str, str]:
        # env: dict[str, str] = {
        #     key: str(value)
        #     for key, value in os.environ.items() 
        #     if key not in ["AWS_PROFILE"]
        # }
        
        env = {}
        for key, value in self.container_env.items():
            if value is None:
                continue
            env[str(key)] = str(value)
        
        tf_data_dir = self.workspace_dir / ".terraform-data"
        tf_plugin_cache = self.workspace_dir / "plugin-cache"
        tf_data_dir.mkdir(parents=True, exist_ok=True)
        tf_plugin_cache.mkdir(parents=True, exist_ok=True)
        
        env.setdefault("TF_IN_AUTOMATION", "true")
        env.setdefault("TF_INPUT", "false")
        env["TF_DATA_DIR"] = str(tf_data_dir)
        env.setdefault("TF_PLUGIN_CACHE_DIR", str(tf_plugin_cache))
        env["TF_WORKSPACE"] = self.get_workspace_name()
        return env
    
    def validate_stack(self):
        try:
            self.run_tofu_command("validate", ["fmt", "-check"])
        except subprocess.CalledProcessError as exc:
            logging.exception(exc)
            return BadPlan(textwrap.dedent(f"""
                OpenTofu formatting failed for {self.stack.stack_type} module.
                stdout:
                {exc.stdout or '<empty>'}

                stderr:
                {exc.stderr or '<empty>'}
            """))
        
        try:
            self.run_tofu_command("validate", ["validate"])
        except subprocess.CalledProcessError as exc:
            return BadPlan(textwrap.dedent(f"""
                OpenTofu validate failed for {self.stack.stack_type} module.
                stdout:
                {exc.stdout or '<empty>'}

                stderr:
                {exc.stderr or '<empty>'}
            """))
    
    def destroy_stack(
            self,
            *,
            timeout: int | None = None,
            auto_approve: bool = True,
    ) -> OpenTofuCommandResult:
        """Destroy all resources managed by this stack."""
        args = ["destroy", "-input=false", "-no-color"]
        if auto_approve:
            args.append("-auto-approve")
        
        args.extend(["-var-file", self.get_tfvars_file()])
        
        try:
            result = self.run_tofu_command("destroy", args, timeout=timeout)
            self.record("destroy", result)
            logging.info("Stack destroyed successfully", extra={"stack": self.stack.stack_namespace_token})
            return result
        except OpenTofuCommandError as exc:
            logging.error("Failed to destroy stack", extra={"error": str(exc), "stack": self.stack.stack_namespace_token})
            raise
    
    def get_state_data(self) -> dict[str, Any]:
        result = self.run_tofu_command("show_state", ["show", "-json"])
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise OpenTofuCommandError("Failed to decode OpenTofu state JSON", result) from exc
    
    def get_resources(self, resource_type: str = None) -> list[dict]:
        resource_datas = common.get_list(self.get_state_data(), ["values", "root_module", "resources"])
        
        if resource_type:
            resource_datas = [
                resource_data
                for resource_data in resource_datas
                if resource_data.get("type") == resource_type
            ]
        
        return resource_datas
    
    def get_arns(self, resource_type: str) -> list[str]:
        return common.filter_empty([
            common.get(resource, ["values", "arn"])
            for resource in self.get_resources(resource_type)
        ])
    
    def get_resource_definitions(self, resource_type: str = None):
        resource_defs = []
        
        with open(self.swizzled_module_file, 'r') as f:
            tf_data = hcl2.load(f)
        
        for resource_def_item in tf_data.get('resource', []):
            for resource_key, resource_def in resource_def_item.items():
                if resource_type:
                    if resource_type == resource_key:
                        resource_defs.append(resource_def)
                else:
                    resource_defs.append(resource_def)
        
        return resource_defs
    
    def import_external_resources(self, resources_to_import: list[tuple[str, str]]):
        for name, value in resources_to_import:
            # Skip import if the resource already exists in state
            resource_type = name.split(".")[0]
            state_resources = self.get_resources(resource_type)
            existing_names = [
                f"{res['type']}.{res['name']}"
                for res in state_resources
                if res["type"] == resource_type
            ]
            if name in existing_names:
                logging.info(f"Skipping import for {name}; already managed in state.")
                continue
            if value:
                result = self.run_tofu_command(f"import {name}", [
                    "import",
                    "-compact-warnings",
                    "-var-file",
                    self.get_tfvars_file(),
                    name,
                    value
                ])
    
    def read_cloudwatch_stack_activity(
            self,
            env_type: EnvironmentType,
            stack_tokens: list[str],
            start_time: float,
            fetch_lambda_logs=False
    ) -> dict[str, Any]:
        """Collect CloudFormation stack events and related AWS logs since ``start_time``.

        Returns a structured dictionary that is easier for downstream LLM prompts to consume.
        The dictionary includes per-stack CloudFormation events, CloudTrail errors, and
        CloudWatch diagnostics, along with optional Lambda logs. An empty dict is returned
        when no useful activity was discovered within the deployment window.
        """
        
        structured_activity: dict[str, Any] = {
            "deployment_window_start": self.start_time,
            "cloudtrail": self.get_cloudtrail_errors(),
            "cloudwatch": self.get_cloudwatch_content()
        }
        
        return structured_activity
    
    def _format_stack_event(self, event: dict) -> str:
        """Return a compact summary string describing a CloudFormation stack event."""
        timestamp = event.get("Timestamp")
        if hasattr(timestamp, "isoformat"):
            timestamp_str = timestamp.isoformat()
        else:
            timestamp_str = str(timestamp) if timestamp is not None else ""
        
        stack_identifier = event.get("StackName") or event.get("StackId") or ""
        logical_id = event.get("LogicalResourceId") or ""
        resource_type = event.get("ResourceType") or ""
        status = event.get("ResourceStatus") or ""
        reason = event.get("ResourceStatusReason") or ""
        physical_id = event.get("PhysicalResourceId") or ""
        client_token = event.get("ClientRequestToken") or ""
        
        parts: list[str] = []
        if timestamp_str:
            parts.append(str(timestamp_str))
        if stack_identifier:
            parts.append(f"Stack: {stack_identifier}")
        if logical_id and resource_type:
            parts.append(f"{logical_id} ({resource_type})")
        elif logical_id:
            parts.append(str(logical_id))
        elif resource_type:
            parts.append(f"ResourceType: {resource_type}")
        if status:
            parts.append(f"Status: {status}")
        if reason:
            parts.append(f"Reason: {reason}")
        if physical_id:
            parts.append(f"PhysicalId: {physical_id}")
        if client_token:
            parts.append(f"Token: {client_token}")
        
        return " | ".join(parts)
    
    def _normalize_stack_event(self, event: dict) -> dict[str, Any]:
        """Return a structured representation of a CloudFormation stack event."""
        timestamp = event.get("Timestamp")
        if hasattr(timestamp, "isoformat"):
            timestamp_str = timestamp.isoformat()
        elif timestamp is not None:
            timestamp_str = str(timestamp)
        else:
            timestamp_str = ""
        
        status = event.get("ResourceStatus")
        status_str = str(status) if status is not None else ""
        
        return {
            "timestamp": timestamp_str,
            "stack_name": event.get("StackName") or event.get("StackId") or "",
            "logical_id": event.get("LogicalResourceId") or "",
            "resource_type": event.get("ResourceType") or "",
            "status": status_str,
            "reason": event.get("ResourceStatusReason") or "",
            "physical_id": event.get("PhysicalResourceId") or "",
            "client_request_token": event.get("ClientRequestToken") or "",
            "is_failure": bool(status_str and "FAILED" in status_str),
            "summary": self._format_stack_event(event)
        }
    
    def get_cloudtrail_errors(self):
        data = {
            "context": "Recent CloudTrail events in this region (last 15 min)",
            "events": [],
            "errors": []
        }
        
        try:
            ct_client = self.cloud_account.get_service_client("cloudtrail")
            ct_query_start_time = self.start_time
            
            ct_events = ct_client.lookup_events(
                StartTime=ct_query_start_time,
                EndTime=common.get_now(),
                MaxResults=100
            )
            
            recent_ct_events = [
                event for event in ct_events.get("Events", [])
                if event['EventTime'] >= self.start_time
            ]
            recent_ct_events.sort(key=lambda x: x['EventTime'])
            
            for ct_event in recent_ct_events:
                cloudtrain_event_data = json.loads(ct_event["CloudTrailEvent"])
                error_message: str = cloudtrain_event_data.get("errorMessage")
                if not error_message:
                    continue
                if "No updates are to be performed" in error_message:
                    continue
                normalized_error = error_message.lower()
                if normalized_error.startswith("stack with id") and normalized_error.endswith("does not exist"):
                    continue
                
                event_time = ct_event.get("EventTime")
                if hasattr(event_time, "isoformat"):
                    event_time_str = event_time.isoformat()
                elif event_time is not None:
                    event_time_str = str(event_time)
                else:
                    event_time_str = ""
                
                data["events"].append({
                    "event_name": ct_event.get("EventName"),
                    "event_time": event_time_str,
                    "error_message": error_message,
                    "event_source": cloudtrain_event_data.get("eventSource"),
                    "request_parameters": cloudtrain_event_data.get("requestParameters"),
                    "response_elements": cloudtrain_event_data.get("responseElements"),
                    "raw_event": cloudtrain_event_data
                })
        except Exception as ct_ex:
            logging.exception(ct_ex)
            data["errors"].append(f"Failed to fetch CloudTrail events: {ct_ex}")
        
        return data
    
    def get_cloudwatch_content(self):
        data = {
            "context": "CloudWatch logs for stack resources since deployment start",
            "errors": []
        }
        
        effective_end_time = to_utc(common.get_now())
        start_epoch = max(0, int(to_utc(self.start_time).timestamp()) - 60)
        end_epoch = int(effective_end_time.timestamp()) + 60
        data["time_window"] = {
            "start_epoch": start_epoch,
            "end_epoch": end_epoch
        }
        
        try:
            data["cloudwatch_logs"] = self.extract_cloudwatch_stack_logs_for_window(
                start_time=start_epoch,
                end_time=end_epoch
            )
            
            ecs_task_stop_reasons = self.extract_ecs_task_stop_reasons()
            if ecs_task_stop_reasons:
                data["ecs_task_stop_reasons"] = ecs_task_stop_reasons
            
            try:
                cloudwatch_alarms = self.extract_cloudwatch_alarms_for_stack()
                if cloudwatch_alarms:
                    data["cloudwatch_alarms"] = cloudwatch_alarms
            except Exception as e:
                logging.exception(e)
            
            try:
                alb_error_logs = self.extract_alb_error_logs(start_epoch, end_epoch)
                if alb_error_logs:
                    data["alb_error_logs"] = alb_error_logs
            except Exception as e:
                logging.exception(e)

        except Exception as stack_log_ex:
            logging.exception(stack_log_ex)
            data["errors"].append(f"Failed to collect CloudWatch logs for stack resources: {stack_log_ex}")
        
        return data
    
    def collect_recent_events(
            self,
            cf_client,
            deployment_start_datetime,
            target_stack: str,
            visited: set[str]
    ) -> tuple[list[dict], list[str]]:
        """Recursively gather stack events for the stack and any nested stacks."""
        local_errors: list[str] = []
        recent: list[dict] = []
        
        identifier = target_stack
        if identifier in visited:
            return recent, local_errors
        
        events: list[dict] = []
        try:
            next_token: str | None = None
            while True:
                request: dict[str, object] = {"StackName": identifier}
                if next_token:
                    request["NextToken"] = next_token
                events_resp = cf_client.describe_stack_events(**request)
                page_events = events_resp.get("StackEvents", [])
                if page_events:
                    events.extend(page_events)
                next_token = events_resp.get("NextToken")
                if not next_token:
                    break
                # Avoid potential infinite pagination loops if AWS returns an empty page with a token
                if not page_events:
                    break
        except Exception as exc:  # pragma: no cover - defensive guard for AWS API quirkiness
            local_errors.append(f"Failed to fetch stack events for {identifier}: {exc}")
            return recent, local_errors
        
        # Mark the stack as visited using both the identifier we queried and any StackId we observe
        visited.add(identifier)
        for evt in events:
            stack_id = evt.get("StackId")
            if stack_id:
                visited.add(stack_id)
        
        # Apply a 60-second buffer before deployment start to ensure we capture all relevant events
        # This matches the CloudWatch log collection buffer used in get_cloudwatch_errors:314
        buffered_start_datetime = deployment_start_datetime - timedelta(seconds=60)
        
        for evt in events:
            timestamp = evt.get("Timestamp")
            if timestamp and timestamp >= buffered_start_datetime:
                recent.append(evt)
        
        # Discover nested stacks so their failure events are included as well
        nested_stack_ids: set[str] = set()
        try:
            paginator = cf_client.get_paginator("list_stack_resources")
            for page in paginator.paginate(StackName=identifier):
                for summary in page.get("StackResourceSummaries", []):
                    if summary.get("ResourceType") != "AWS::CloudFormation::Stack":
                        continue
                    nested_identifier = summary.get("PhysicalResourceId") or summary.get("StackId")
                    if nested_identifier and nested_identifier not in visited:
                        nested_stack_ids.add(nested_identifier)
        except Exception as nested_exc:  # pragma: no cover - best effort, do not fail entire call
            local_errors.append(f"Failed to enumerate nested stacks for {identifier}: {nested_exc}")
        
        for nested_identifier in nested_stack_ids:
            nested_events, nested_errors = self.collect_recent_events(
                cf_client,
                deployment_start_datetime,
                nested_identifier,
                visited
            )
            
            recent.extend(nested_events)
            local_errors.extend(nested_errors)
        
        return recent, local_errors
    
    def extract_ecs_task_stop_reasons(
            self,
            max_clusters: int = 10,
            max_services: int = 20,
            max_tasks: int = 30
    ) -> str:
        """
        Extracts ECS task stop reasons for services whose names contain the stack name.
        Returns a formatted string with taskArn and reasons.
        """
        ecs = self.cloud_account.get_service_client("ecs")
        clusters_resp = ecs.list_clusters()
        cluster_arns = clusters_resp.get("clusterArns", [])[:max_clusters]
        
        found_service_arns = []
        cluster_for_service = {}
        for cluster_arn in cluster_arns:
            try:
                paginator = ecs.get_paginator("list_services")
                for page in paginator.paginate(cluster=cluster_arn):
                    for service_arn in page.get("serviceArns", []):
                        svc_name = service_arn.split("/")[-1]
                        if self.stack.stack_namespace_token in svc_name:
                            found_service_arns.append(service_arn)
                            cluster_for_service[service_arn] = cluster_arn
                            if len(found_service_arns) >= max_services:
                                break
                    if len(found_service_arns) >= max_services:
                        break
            except Exception as e:
                logging.info(f"Failed to list ECS services in cluster {cluster_arn}: {e}")
                continue
            if len(found_service_arns) >= max_services:
                break
        
        if not found_service_arns:
            return ""
        
        lines = []
        for service_arn in found_service_arns:
            cluster_arn = cluster_for_service[service_arn]
            try:
                task_arns_resp = ecs.list_tasks(cluster=cluster_arn, serviceName=service_arn, desiredStatus="STOPPED")
                task_arns = task_arns_resp.get("taskArns", [])[:max_tasks]
            except Exception as e:
                logging.info(f"Failed to list ECS tasks for service {service_arn}: {e}")
                continue
            if not task_arns:
                continue
            try:
                desc = ecs.describe_tasks(cluster=cluster_arn, tasks=task_arns)
                for task in desc.get("tasks", []):
                    task_arn = task.get("taskArn", "")
                    stopped_reason = task.get("stoppedReason", "")
                    containers = task.get("containers", [])
                    lines.append(f"Task: {task_arn}")
                    if stopped_reason:
                        lines.append(f"  stoppedReason: {stopped_reason}")
                    for cont in containers:
                        cname = cont.get("name", "")
                        exit_code = cont.get("exitCode")
                        reason = cont.get("reason", "")
                        if exit_code is not None or reason:
                            lines.append(f"    Container: {cname} exitCode={exit_code} reason={reason}")
            except Exception as e:
                logging.info(f"Failed to describe ECS tasks for service {service_arn}: {e}")
                continue
        return "\n".join(lines) if lines else ""
    
    def extract_cloudwatch_alarms_for_stack(self) -> str:
        """
        Returns CloudWatch alarms in ALARM state whose names or ARNs contain the stack name.
        """
        resp = self.cloud_account.get_service_client(
            "cloudwatch"
        ).describe_alarms(
            StateValue='ALARM'
        )
        
        alarms = resp.get("MetricAlarms", []) + resp.get("CompositeAlarms", [])
        found = []
        for alarm in alarms:
            name = alarm.get("AlarmName", "")
            arn = alarm.get("AlarmArn", "")
            state_reason = alarm.get("StateReason", "")
            if self.stack.stack_namespace_token in name:
                found.append(f"{name} — {state_reason}")
        return "\n".join(found) if found else ""
    
    def extract_alb_error_logs(
            self,
            start_time: int,
            end_time: int,
            max_groups: int = 10
    ) -> str:
        """
        Searches CloudWatch log groups for ALB logs (log group names starting with
        /aws/elasticloadbalancing or /aws/applicationloadbalancer) and queries for entries
        containing "error" or "5xx" in message fields, for groups that contain the stack name.
        """
        logs = self.cloud_account.get_service_client("logs")
        log_groups = []
        next_token = None
        prefixes = ["/aws/elasticloadbalancing", "/aws/applicationloadbalancer"]
        try:
            for prefix in prefixes:
                next_token = None
                while True:
                    kwargs = {"logGroupNamePrefix": prefix}
                    if next_token:
                        kwargs["nextToken"] = next_token
                    resp = logs.describe_log_groups(**kwargs)
                    for lg in resp.get("logGroups", []):
                        name = lg.get("logGroupName")
                        if self.stack.stack_namespace_token in name:
                            log_groups.append(name)
                            if len(log_groups) >= max_groups:
                                break
                    if len(log_groups) >= max_groups or not resp.get("nextToken"):
                        break
                    next_token = resp.get("nextToken")
        except Exception as e:
            logging.info(f"Failed to list ALB log groups: {e}")
            return ""
        if not log_groups:
            return ""
        query_str = (
            "fields @timestamp, @log, @message "
            "| filter @message like /error|5[0-9][0-9]/ "
            "| sort @timestamp asc "
            "| limit 1000"
        )
        combined = []
        batch_size = 5
        for i in range(0, len(log_groups), batch_size):
            batch = log_groups[i:i + batch_size]
            try:
                q = logs.start_query(
                    logGroupNames=batch,
                    startTime=int(start_time),
                    endTime=int(end_time),
                    queryString=query_str
                )
                query_id = q["queryId"]
            except Exception as e:
                logging.info(f"start_query failed for ALB log group batch {batch}: {e}")
                continue
            # Poll for completion
            status = "Running"
            for _ in range(60):
                time.sleep(1)
                resp = logs.get_query_results(queryId=query_id)
                status = resp.get("status")
                if status in ("Complete", "Failed", "Cancelled", "Timeout"):
                    results = resp.get("results", [])
                    for item in results:
                        fields = {f.get("field"): f.get("value") for f in item}
                        ts = fields.get("@timestamp", "")
                        lg = fields.get("@log", "")
                        msg = fields.get("@message", "")
                        combined.append(f"{lg} | {ts}\n{msg}")
                    break
            if status != "Complete":
                logging.info(f"Logs Insights query did not complete (status={status}) for ALB log group batch {batch}")
        return "\n\n".join(combined) if combined else ""
    
    def extract_cloudwatch_stack_logs_for_window(
            self,
            start_time: int,
            end_time: int,
            max_groups: int = 50
    ) -> str:
        window_start_dt = datetime.fromtimestamp(int(start_time), tz=timezone.utc)
        
        log_groups = self.get_log_groups()
        if not log_groups:
            logging.info(f"No matching log groups found for stack tokens {self.stack.stack_namespace_token}")
            return ""
        
        # Logs Insights query over the time window - no RequestId filter, just the window
        query_str = (
            "fields @timestamp, @log, @message "
            "| sort @timestamp asc "
            "| limit 2000"
        )
        
        logs = self.cloud_account.get_service_client("logs")
        query_id = logs.start_query(
            logGroupNames=log_groups,
            startTime=int(start_time),
            endTime=int(end_time),
            queryString=query_str
        )["queryId"]
        
        status = "Running"
        log_results = []
        for _ in range(60):
            time.sleep(1)
            resp = logs.get_query_results(queryId=query_id)
            status = resp.get("status")
            if status in ("Complete", "Failed", "Cancelled", "Timeout"):
                results = resp.get("results", [])
                for item in results:
                    fields = {f.get("field"): f.get("value") for f in item}
                    ts = fields.get("@timestamp", "")
                    lg = fields.get("@log", "")
                    msg = fields.get("@message", "")
                    log_results.append(f"{ts}  {lg}\n{msg}")
                break
        
        if status != "Complete":
            logging.info(f"Logs Insights window query did not complete (status={status}) ")
        
        return "\n".join(log_results)
    
    def get_log_groups(self):
        logs = self.cloud_account.get_service_client("logs")
        log_groups = []
        next_token = None
        while True:
            kwargs = {"logGroupNamePrefix": "/"}  # or any known prefix
            if next_token:
                kwargs["nextToken"] = next_token
            resp = logs.describe_log_groups(**kwargs)
            for lg in resp.get("logGroups", []):
                name = lg["logGroupName"]
                if self.stack.stack_namespace_token in name:
                    log_groups.append(name)
            if "nextToken" not in resp:
                break
            next_token = resp["nextToken"]
        
        return log_groups
    
    def get_db_is_running(self) -> bool:
        def _internal_db_is_running() -> bool:
            """
            Return True if an RDS instance exists in the current OpenTofu-managed state
            and is in a healthy/available state according to AWS.
            """
            # First, check whether Terraform state contains any RDS instance resources.
            rds_resources = self.get_resources("aws_db_instance")
            if not rds_resources:
                return False

            # If Terraform state has an RDS instance, verify it exists and is available in AWS.
            rds_client = self.cloud_account.get_service_client("rds")

            for res in rds_resources:
                db_id = res.get("values", {}).get("identifier")
                if not db_id:
                    continue
                    
                try:
                    resp = rds_client.describe_db_instances(DBInstanceIdentifier=db_id)
                    instances = resp.get("DBInstances", [])
                    if not instances:
                       continue
                        
                    status = instances[0].get("DBInstanceStatus", "")
                    if status.lower() in ("available", "backing-up", "backtracking"):
                        return True
                    
                except Exception as e:
                    logging.exception(e)
                    continue

            return False
        
        for i in range(3):
            if _internal_db_is_running():
                return True
            else:
                time.sleep(10)
                
        return _internal_db_is_running()
