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
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

import hcl2

import settings
from erieiron_autonomous_agent.coding_agents.self_driving_coder_config import BadPlan
from erieiron_autonomous_agent.models import InfrastructureStack
from erieiron_common import common, opentofu_log_utils
from erieiron_common.enums import InfrastructureStackType, EnvironmentType
from erieiron_common.opentofu_helpers import OpenTofuVariable, OpenTofuCommandResult, OpenTofuCommandError, OpenTofuException
from erieiron_common.opentofu_log_utils import OpenTofuRunResult


class OpenTofuStackManager:
    def __init__(
            self,
            stack: InfrastructureStack,
            sandbox_root_dir: Path,
            container_env: dict
    ):
        self.stack = stack
        self.sandbox_root_dir = sandbox_root_dir
        self.container_env = container_env
        self.stack_type = InfrastructureStackType(self.stack.stack_type)
        self.module_file = get_swizzled_module_file(stack, sandbox_root_dir)
        self.module_dir = self.module_file.parent
        self.tf_env = self.build_opentofu_env()
        
        self.full_env: MutableMapping[str, str] = os.environ.copy()
        self.full_env.update(self.container_env)
        self.full_env.update(self.tf_env)
        
        self.stage = "init"
        self.run_results: list[OpenTofuRunResult] = []
    
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
        workdir = str(self.get_workspace_dir())
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
        
        print(f"""
        
        
        {completed_process.stderr}
        
        
        """)
        
        if result.returncode != 0:
            logging.error("OpenTofu command failed", extra=result.loggable_dict())
            raise OpenTofuCommandError(
                f"OpenTofu command failed with exit code {result.returncode}",
                result,
            )
        
        logging.debug("OpenTofu command completed", extra=result.loggable_dict())
        self.record(stage, result)
        return result
    
    def init_workspace(
            self,
            *,
            backend_config: Mapping[str, str] | None = None,
            timeout: int | None = None,
            upgrade: bool = False,
    ) -> OpenTofuCommandResult:
        args = ["init", "-input=false", "-no-color"]
        
        if upgrade:
            args.append("-upgrade")
        
        for key, value in sorted((backend_config or {}).items()):
            args.append(f"-backend-config={key}={value}")
        
        return self.run_tofu_command(
            "init",
            args,
            timeout=timeout
        )
    
    def plan(
            self,
            *,
            var_files: Iterable[Path] | None = None,
            timeout: int | None = None,
            destroy: bool = False,
            refresh: bool = True,
            json_output: bool = True,
            plan_output_path: Path | None = None,
    ) -> OpenTofuCommandResult:
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        plan_path: Path | None = None
        try:
            if json_output:
                if plan_output_path is not None:
                    plan_path = plan_output_path
                    plan_path.parent.mkdir(parents=True, exist_ok=True)
                else:
                    temp_dir = tempfile.TemporaryDirectory()
                    plan_path = Path(temp_dir.name) / "plan.tfplan"
            
            args = ["plan", "-input=false", "-no-color"]
            if plan_path:
                args.extend(["-out", str(plan_path)])
            if destroy:
                args.append("-destroy")
            if not refresh:
                args.append("-refresh=false")
            for vf in common.ensure_list(var_files):
                args.extend(["-var-file", str(vf)])
            
            result = self.run_tofu_command(
                "plan",
                args,
                timeout=timeout
            )
            
            if json_output and plan_path and plan_path.exists():
                show_result = self.run_tofu_command(
                    "show",
                    ["show", "-json", str(plan_path)],
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
                result.extra["plan_path"] = str(plan_path)
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
            plan_path: Path | None = None,
            retries: int = 0,
            retry_backoff_seconds: float = 5.0,
    ) -> OpenTofuCommandResult:
        args = ["apply", "-input=false", "-no-color"]
        if auto_approve:
            args.append("-auto-approve")
        if plan_path:
            args.append(str(plan_path))
        
        attempt = 0
        exc: OpenTofuCommandError | None = None
        while attempt <= max(retries, 0):
            try:
                return self.run_tofu_command(
                    "apply",
                    args,
                    timeout=timeout
                )
            except OpenTofuCommandError as error:
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
                cwd=self.get_workspace_dir(),
                started_at=common.get_now(),
                completed_at=common.get_now(),
                returncode=-1,
                stdout="",
                stderr="apply aborted without execution",
            ),
        )
    
    def output_json(
            self,
            *,
            timeout: int | None = None,
    ) -> dict[str, Any]:
        result = self.run_tofu_command(
            self.stage,
            ["output", "-json"],
            timeout=timeout,
        )
        try:
            outputs = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise OpenTofuCommandError("Failed to decode OpenTofu outputs", result) from exc
        result.extra["outputs"] = outputs
        return outputs
    
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
    
    def get_workspace_dir(self) -> Path:
        workspace_dir = self.sandbox_root_dir / "opentofu" / "workspaces" / self.get_workspace_name()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return workspace_dir
    
    def get_state_file(self):
        return str(self.get_workspace_dir() / "terraform.tfstate"),
    
    def get_state_locator(self):
        return f"opentofu://workspace/{self.get_workspace_name()}"
    
    @staticmethod
    def get_stack_variables(stack: InfrastructureStack, sandbox_root_dir: Path) -> dict[str, OpenTofuVariable]:
        tf_path = get_swizzled_module_file(stack, sandbox_root_dir)
        
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
        tfvars_path = self.get_workspace_dir() / suffix
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
        
        workspace_dir = self.get_workspace_dir()
        tf_data_dir = workspace_dir / ".terraform-data"
        tf_plugin_cache = workspace_dir / "plugin-cache"
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
            self.run_tofu_command("validate", ["tofu", "fmt", "-check"])
        except subprocess.CalledProcessError as exc:
            return BadPlan(textwrap.dedent(f"""
                OpenTofu formatting failed for {self.stack.stack_type} module.
                stdout:
                {exc.stdout or '<empty>'}

                stderr:
                {exc.stderr or '<empty>'}
            """))
        
        try:
            self.run_tofu_command("validate", ["tofu", "validate"])
        except subprocess.CalledProcessError as exc:
            return BadPlan(textwrap.dedent(f"""
                OpenTofu validate failed for {self.stack.stack_type} module.
                stdout:
                {exc.stdout or '<empty>'}

                stderr:
                {exc.stderr or '<empty>'}
            """))


def get_swizzled_module_file(
        stack: InfrastructureStack,
        sandbox_root_dir: Path
):
    tf_path = common.assert_exists(
        sandbox_root_dir / InfrastructureStackType(stack.stack_type).get_opentofu_config()
    )
    
    with tf_path.open("r", encoding="utf-8") as f:
        contents = f.read()
    
    prevent_destroy = EnvironmentType.PRODUCTION.eq(stack.env_type)
    new_contents = contents.replace("ERIE_IRON_RETAIN_RESOURCES", "true" if prevent_destroy else "false")
    
    temp_dir = tempfile.mkdtemp(prefix="opentofu_swizzle_")
    swizzled_path = Path(temp_dir) / tf_path.name
    
    with swizzled_path.open("w", encoding="utf-8") as f:
        f.write(new_contents)
    
    return swizzled_path
