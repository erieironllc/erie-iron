"""Utilities for working with OpenTofu (Terraform) configurations."""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from erieiron_common import aws_utils, common
from erieiron_common.enums import InfrastructureStackType

LOGGER = logging.getLogger(__name__)
_TOFU_BIN = os.environ.get("OPENTOFU_BIN", "tofu")

try:  # pragma: no cover - import guarded for clarity during package installation
    import hcl2  # type: ignore
except ImportError as exc:  # pragma: no cover - surfaces configuration errors quickly
    hcl2 = None
    HCL2_IMPORT_ERROR = exc
else:  # pragma: no cover - attribute only set when import succeeds
    HCL2_IMPORT_ERROR = None


class OpenTofuException(Exception):
    """Base exception for OpenTofu helper errors."""


class OpenTofuCommandError(OpenTofuException):
    """Raised when an OpenTofu CLI invocation fails."""

    def __init__(self, message: str, result: "OpenTofuCommandResult"):
        super().__init__(message)
        self.result = result


class OpenTofuStackObsolete(OpenTofuException):
    """Raised when a stack must be rotated before continuing."""


@dataclass(frozen=True)
class OpenTofuModuleEntry:
    """Describes the location of a module on disk."""

    stack_type: InfrastructureStackType
    module_path: Path
    workdir: Path
    entrypoint: Path
    exists: bool


@dataclass(frozen=True)
class OpenTofuStackConfig:
    """Captures module + workspace metadata for a stack instance."""

    stack_type: InfrastructureStackType
    module: OpenTofuModuleEntry
    workspace_name: str
    workspace_dir: Path


@dataclass(frozen=True)
class OpenTofuVariable:
    """Represents a declared variable within an OpenTofu module."""

    name: str
    required: bool
    default: Any
    type_expression: Any
    description: str | None
    sensitive: bool
    source_file: str | None = None


@dataclass
class OpenTofuCommandResult:
    """Represents the outcome of a tofu CLI command."""

    command: Sequence[str]
    cwd: Path
    started_at: datetime
    completed_at: datetime
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float = field(init=False)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "duration_seconds",
            max((self.completed_at - self.started_at).total_seconds(), 0.0),
        )

    def loggable_dict(self) -> dict[str, Any]:
        return {
            "command": " ".join(shlex.quote(part) for part in self.command),
            "cwd": str(self.cwd),
            "returncode": self.returncode,
            "duration_seconds": self.duration_seconds,
            "stdout": self.stdout.strip(),
            "stderr": self.stderr.strip(),
            "extra": self.extra,
        }


def get_modules_root(repo_root: Path) -> Path:
    modules_root = repo_root / "opentofu"
    modules_root.mkdir(parents=True, exist_ok=True)
    return modules_root


def resolve_module_entry(repo_root: Path, stack_type: InfrastructureStackType) -> OpenTofuModuleEntry:
    modules_root = get_modules_root(repo_root)
    module_token = stack_type.value.lower()
    folder_candidate = modules_root / module_token
    file_candidate = modules_root / f"{module_token}.tf"

    if folder_candidate.is_dir():
        entrypoint = folder_candidate / "main.tf"
        exists = entrypoint.exists() or any(folder_candidate.glob("*.tf"))
        if not entrypoint.exists():
            entrypoint = folder_candidate
        return OpenTofuModuleEntry(
            stack_type=stack_type,
            module_path=folder_candidate,
            workdir=folder_candidate,
            entrypoint=entrypoint,
            exists=exists,
        )

    if file_candidate.exists():
        return OpenTofuModuleEntry(
            stack_type=stack_type,
            module_path=file_candidate,
            workdir=modules_root,
            entrypoint=file_candidate,
            exists=True,
        )

    # Create a directory placeholder so future iterations have a canonical location.
    folder_candidate.mkdir(parents=True, exist_ok=True)
    return OpenTofuModuleEntry(
        stack_type=stack_type,
        module_path=folder_candidate,
        workdir=folder_candidate,
        entrypoint=folder_candidate,
        exists=False,
    )


def compute_workspace_name(stack_namespace_token: str, stack_type: InfrastructureStackType) -> str:
    base = aws_utils.sanitize_aws_name(stack_namespace_token, max_length=40)
    suffix = aws_utils.sanitize_aws_name(stack_type.value.lower(), max_length=20)
    return aws_utils.sanitize_aws_name(f"{base}-{suffix}", max_length=63)


def get_workspace_root(repo_root: Path) -> Path:
    workspace_root = get_modules_root(repo_root) / "workspaces"
    workspace_root.mkdir(parents=True, exist_ok=True)
    return workspace_root


def ensure_workspace(repo_root: Path, stack_namespace_token: str, stack_type: InfrastructureStackType) -> OpenTofuStackConfig:
    module_entry = resolve_module_entry(repo_root, stack_type)
    workspace_name = compute_workspace_name(stack_namespace_token, stack_type)
    workspace_dir = get_workspace_root(repo_root) / workspace_name
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return OpenTofuStackConfig(
        stack_type=stack_type,
        module=module_entry,
        workspace_name=workspace_name,
        workspace_dir=workspace_dir,
    )


def ensure_workspaces(repo_root: Path, stack_namespace_map: Mapping[InfrastructureStackType, str]) -> dict[InfrastructureStackType, OpenTofuStackConfig]:
    configs: dict[InfrastructureStackType, OpenTofuStackConfig] = {}
    for stack_type, namespace in stack_namespace_map.items():
        if not namespace:
            continue
        configs[stack_type] = ensure_workspace(repo_root, namespace, stack_type)
    return configs


def discover_modules(repo_root: Path) -> dict[InfrastructureStackType, OpenTofuModuleEntry]:
    return {
        stack_type: resolve_module_entry(repo_root, stack_type)
        for stack_type in InfrastructureStackType
    }


def _assert_hcl2_available() -> None:
    if hcl2 is None:  # pragma: no cover - executed only when dependency missing
        raise OpenTofuException(
            "python-hcl2 is required to parse OpenTofu modules but could not be imported"
        ) from HCL2_IMPORT_ERROR


def _iter_module_tf_files(module: OpenTofuModuleEntry) -> Iterable[Path]:
    if module.entrypoint.is_file() and module.entrypoint.suffix == ".tf":
        yield module.entrypoint
    if module.workdir.is_dir():
        for tf_path in sorted(module.workdir.glob("*.tf")):
            if tf_path != module.entrypoint:
                yield tf_path


def load_module_variables(module: OpenTofuModuleEntry) -> dict[str, OpenTofuVariable]:
    """Parse all Terraform variable blocks within the module."""

    _assert_hcl2_available()
    variables: dict[str, OpenTofuVariable] = {}

    for tf_path in _iter_module_tf_files(module):
        try:
            with tf_path.open("r", encoding="utf-8") as tf_file:
                parsed = hcl2.load(tf_file)  # type: ignore[arg-type]
        except FileNotFoundError:
            continue
        except Exception as exc:  # pragma: no cover - defensive error surfacing
            raise OpenTofuException(
                f"Failed to parse Terraform file '{tf_path}'"
            ) from exc

        for variable_block in parsed.get("variable", []) or []:
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


def get_tfvars_path(stack_config: OpenTofuStackConfig, filename: str | None = None) -> Path:
    suffix = filename or f"{stack_config.stack_type.value.lower()}.auto.tfvars.json"
    return stack_config.workspace_dir / suffix


def write_tfvars_file(
    stack_config: OpenTofuStackConfig,
    variables: Mapping[str, Any],
    *,
    filename: str | None = None,
) -> Path:
    tfvars_path = get_tfvars_path(stack_config, filename=filename)
    tfvars_path.parent.mkdir(parents=True, exist_ok=True)
    common.write_json(tfvars_path, variables)
    return tfvars_path


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


def normalize_outputs(raw_outputs: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in raw_outputs.items():
        if isinstance(value, Mapping) and "value" in value:
            normalized[key] = value.get("value")
        else:
            normalized[key] = value
    return normalized


def _build_env(env: Mapping[str, str] | None) -> MutableMapping[str, str]:
    base_env: MutableMapping[str, str] = os.environ.copy()
    if env:
        base_env.update(env)
    return base_env


def _run_tofu_command(
    args: Sequence[str],
    *,
    workdir: Path,
    env: Mapping[str, str] | None = None,
    input_data: str | None = None,
    timeout: int | None = None,
) -> OpenTofuCommandResult:
    command = [_TOFU_BIN, *args]
    started_at = datetime.utcnow()
    LOGGER.debug("Running OpenTofu command", extra={"command": command, "cwd": str(workdir)})
    try:
        completed_process = subprocess.run(
            command,
            cwd=str(workdir),
            input=input_data,
            text=True,
            capture_output=True,
            env=_build_env(env),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        completed_at = datetime.utcnow()
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

    completed_at = datetime.utcnow()
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
        LOGGER.error("OpenTofu command failed", extra=result.loggable_dict())
        raise OpenTofuCommandError(
            f"OpenTofu command failed with exit code {result.returncode}",
            result,
        )

    LOGGER.debug("OpenTofu command completed", extra=result.loggable_dict())
    return result


def init_workspace(
    config: OpenTofuStackConfig,
    *,
    backend_config: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int | None = None,
    upgrade: bool = False,
) -> OpenTofuCommandResult:
    args = ["init", "-input=false", "-no-color"]
    if upgrade:
        args.append("-upgrade")
    for key, value in sorted((backend_config or {}).items()):
        args.append(f"-backend-config={key}={value}")
    return _run_tofu_command(args, workdir=config.module.workdir, env=env, timeout=timeout)


def plan(
    config: OpenTofuStackConfig,
    *,
    var_files: Iterable[Path] | None = None,
    env: Mapping[str, str] | None = None,
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

        result = _run_tofu_command(args, workdir=config.module.workdir, env=env, timeout=timeout)

        if json_output and plan_path and plan_path.exists():
            show_result = _run_tofu_command(
                ["show", "-json", str(plan_path)],
                workdir=config.module.workdir,
                env=env,
                timeout=timeout,
            )
            try:
                plan_json = json.loads(show_result.stdout or "{}")
            except json.JSONDecodeError as exc:
                raise OpenTofuCommandError(
                    "Failed to decode OpenTofu plan JSON",
                    show_result,
                ) from exc
            change_summary = summarize_plan_changes(plan_json)
            result.extra["plan_path"] = str(plan_path)
            result.extra["plan_json"] = plan_json
            result.extra["plan_change_summary"] = change_summary
            result.extra["show_result"] = show_result.loggable_dict()
        return result
    finally:
        if temp_dir:
            temp_dir.cleanup()


def apply(
    config: OpenTofuStackConfig,
    *,
    env: Mapping[str, str] | None = None,
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
            return _run_tofu_command(args, workdir=config.module.workdir, env=env, timeout=timeout)
        except OpenTofuCommandError as error:
            exc = error
            attempt += 1
            if attempt > max(retries, 0):
                break
            LOGGER.warning(
                "OpenTofu apply failed; retrying",
                extra={
                    "attempt": attempt,
                    "retries": retries,
                    "command": " ".join(shlex.quote(part) for part in args),
                    "workspace": config.workspace_name,
                },
            )
            time.sleep(retry_backoff_seconds * attempt)
    if exc:
        raise exc
    raise OpenTofuCommandError(
        "OpenTofu apply failed without raising an exception",
        OpenTofuCommandResult(
            command=[_TOFU_BIN, *args],
            cwd=config.module.workdir,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            returncode=-1,
            stdout="",
            stderr="apply aborted without execution",
        ),
    )


def output_json(
    config: OpenTofuStackConfig,
    *,
    env: Mapping[str, str] | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    result = _run_tofu_command(
        ["output", "-json"],
        workdir=config.module.workdir,
        env=env,
        timeout=timeout,
    )
    try:
        outputs = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise OpenTofuCommandError("Failed to decode OpenTofu outputs", result) from exc
    result.extra["outputs"] = outputs
    return outputs


def summarize_plan_changes(plan_json: Mapping[str, Any]) -> Mapping[str, int]:
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


__all__ = [
    "OpenTofuException",
    "OpenTofuCommandError",
    "OpenTofuStackObsolete",
    "OpenTofuCommandResult",
    "OpenTofuModuleEntry",
    "OpenTofuStackConfig",
    "OpenTofuVariable",
    "compute_workspace_name",
    "discover_modules",
    "ensure_workspace",
    "ensure_workspaces",
    "get_modules_root",
    "get_workspace_root",
    "get_tfvars_path",
    "load_module_variables",
    "init_workspace",
    "plan",
    "apply",
    "output_json",
    "resolve_module_entry",
    "summarize_plan_changes",
    "validate_required_variables",
    "write_tfvars_file",
    "normalize_outputs",
]
