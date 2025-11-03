"""Utilities for working with OpenTofu (Terraform) configurations."""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

import hcl2

import settings
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import BadPlan
from erieiron_autonomous_agent.models import InfrastructureStack
from erieiron_common import common, opentofu_log_utils
from erieiron_common.enums import InfrastructureStackType, EnvironmentType
from erieiron_common.opentofu_helpers import OpenTofuVariable, OpenTofuCommandResult, OpenTofuCommandError, OpenTofuException
from erieiron_common.opentofu_log_utils import OpenTofuRunResult


class OpenTofuStackManager:
    def __init__(
            self,
            stack: InfrastructureStack,
            container_env: dict = None,
            sandbox_root: Path = None
    ):
        
        self.stack = stack
        self.container_env = container_env or {}
        self.stack_type = InfrastructureStackType(self.stack.stack_type)
        self.sandbox_root = sandbox_root or Path(tempfile.mkdtemp(prefix=f"opentofu_temp_root_{self.stack_type}".lower()))
        
        self.workspace_dir = common.mkdirs(self.sandbox_root / "opentofu" / "workspaces" / self.get_workspace_name())
        self.module_dir = common.mkdirs(self.sandbox_root / "opentofu" / "swizzled_modules" / self.stack_type.value.lower())
        
        self.swizzled_module_file = self.get_swizzled_module_file()

        self.tf_env = self.build_opentofu_env()
        
        self.full_env: MutableMapping[str, str] = os.environ.copy()
        self.full_env.update(self.container_env)
        self.full_env.update(self.tf_env)
        
        self.stage = "init"
        self.run_results: list[OpenTofuRunResult] = []
        
        self.tfvars_path = self.write_tfvars_file(
            self.stack.stack_vars
        )
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
        command = [settings.TOFU_BIN, f"-chdir={self.module_dir}", *args]
        started_at = common.get_now()
        logging.debug("Running OpenTofu command", extra={"command": command, "cwd": str(workdir)})
        try:
            completed_process = subprocess.run(
                command,
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
        args.append(f"-backend-config=key={key_value}")
        args.append(f"-backend-config=region={EnvironmentType(self.stack.env_type).get_aws_region()}")
        
        last_exception = None
        
        for attempt in range(3):
            try:
                return self.run_tofu_command("init", args)
            except OpenTofuCommandError as e:
                if "state data in S3 does not have the expected content" in e.result.stderr:
                    time.sleep(10 * (attempt + 1))
                    continue
                last_exception = e
        
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
            for vf in common.ensure_list(self.tfvars_path):
                args.extend(["-var-file", str(vf)])
            
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
        for vf in common.ensure_list(self.tfvars_path):
            refresh_args.extend(["-var-file", str(vf)])
        
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
        from erieiron_common import aws_utils
        
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
    
    def write_tfvars_file(
            self,
            variables: Mapping[str, Any],
            *,
            filename: str | None = None,
    ) -> Path:
        suffix = filename or f"{self.stack.stack_type.lower()}.auto.tfvars.json"
        tfvars_path = self.workspace_dir / suffix
        tfvars_path.parent.mkdir(parents=True, exist_ok=True)
        common.write_json(tfvars_path, variables)
        return tfvars_path
    
    def build_opentofu_env(self) -> dict[str, str]:
        env: dict[str, str] = {
            key: str(value)
            for key, value in os.environ.items()
        }
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
        for vf in self.tfvars_path:
            args.extend(["-var-file", str(vf)])
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
    
    def get_resources(self, resource_type: str = None) -> list[str]:
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
    
    @staticmethod
    def get_cross_stack_resources(stack_managers: list['OpenTofuStackManager'], resource_type: str = None) -> list[str]:
        resources = []
        for stack_manager in stack_managers:
            resources += stack_manager.get_resources(resource_type)
        return resources
    
    @staticmethod
    def get_cross_stack_arns(stack_managers: list['OpenTofuStackManager'], resource_type: str) -> list[str]:
        arns = []
        for stack_manager in stack_managers:
            arns += stack_manager.get_arns(resource_type)
        return arns
    
    @staticmethod
    def get_cross_stack_resource_definitions(stack_managers: list['OpenTofuStackManager'], resource_type: str = None):
        resource_defs = []
        for stack_manager in stack_managers:
            resource_defs += stack_manager.get_resource_definitions(resource_type)
        return resource_defs
