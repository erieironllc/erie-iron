from __future__ import annotations

import copy
import logging
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

import settings
from erieiron_common import common
from erieiron_common.enums import (
    TaskImplementationSourceKind,
    WorkflowDefinitionSourceKind,
)
from erieiron_common.git_utils import GitWrapper
from erieiron_common.runtime_config import RuntimeConfig


CONFIG_SCHEMA_VERSION = 1
CONFIG_ROOT = Path("configuration")
TASKS_ROOT = CONFIG_ROOT / "tasks"
WORKFLOWS_ROOT = CONFIG_ROOT / "workflows"
CONFIG_FILENAME = "config.json"
APPLICATION_REPO_CACHE_ROOT = Path(settings.BASE_DIR) / ".application_repo_cache"
APPLICATION_REPO_SYNC_REVISION_PREFIX = "APPLICATION_REPO_SYNC_REVISION"
DEFAULT_APPLICATION_REPO_REF = "main"


def build_task_directory_relative_path(task_id: str) -> str:
    return (
        TASKS_ROOT / common.assert_not_empty(common.default_str(task_id).strip())
    ).as_posix()


def build_task_config_relative_path(task_id: str) -> str:
    return (
        TASKS_ROOT
        / common.assert_not_empty(common.default_str(task_id).strip())
        / CONFIG_FILENAME
    ).as_posix()


def build_workflow_directory_relative_path(workflow_id: str) -> str:
    return (
        WORKFLOWS_ROOT / common.assert_not_empty(common.default_str(workflow_id).strip())
    ).as_posix()


def build_workflow_config_relative_path(workflow_id: str) -> str:
    return (
        WORKFLOWS_ROOT
        / common.assert_not_empty(common.default_str(workflow_id).strip())
        / CONFIG_FILENAME
    ).as_posix()


def normalize_task_bundle_path(
        relative_path: str | None,
        *,
        allow_none: bool = True,
) -> str | None:
    raw_value = common.default_str(relative_path).strip()
    if not raw_value:
        if allow_none:
            return None
        raise ValidationError("task asset path is required")

    normalized_path = PurePosixPath(raw_value)
    if normalized_path.is_absolute():
        raise ValidationError("task asset path must be relative to the task directory")

    normalized_parts = normalized_path.parts
    if not normalized_parts:
        raise ValidationError("task asset path is required")
    if any(part in ("", ".", "..") for part in normalized_parts):
        raise ValidationError("task asset path must stay within the task directory")

    return normalized_path.as_posix()


def resolve_repo_relative_path(repo_root: Path, relative_path: str) -> Path:
    repo_root = Path(repo_root).resolve()
    resolved_path = (
        repo_root / common.assert_not_empty(common.default_str(relative_path).strip())
    ).resolve()
    if repo_root not in resolved_path.parents and resolved_path != repo_root:
        raise ValidationError("path must stay within the application repo")
    return resolved_path


def resolve_task_bundle_path(repo_root: Path, task_id: str, relative_path: str | None) -> Path:
    task_dir = resolve_repo_relative_path(
        repo_root,
        build_task_directory_relative_path(task_id),
    )
    normalized_path = normalize_task_bundle_path(relative_path, allow_none=False)
    resolved_path = (task_dir / normalized_path).resolve()
    if task_dir not in resolved_path.parents and resolved_path != task_dir:
        raise ValidationError("task asset path must stay within the task directory")
    return resolved_path


def get_application_repo_checkout_path(business) -> Path:
    business_key = common.default_str(getattr(business, "service_token", "")).strip()
    if not business_key:
        business_key = str(getattr(business, "id"))
    return APPLICATION_REPO_CACHE_ROOT / common.safe_filename(business_key)


def _refresh_existing_application_repo_checkout(git: GitWrapper, *, pull: bool) -> None:
    if not pull:
        return
    if git.has_head():
        git.pull()
        return

    bootstrapped_branch = git.bootstrap_from_remote_head()
    if bootstrapped_branch:
        logging.info(
            "bootstrapped application repo checkout %s onto remote default branch %s",
            git.source_root,
            bootstrapped_branch,
        )
        return

    logging.info(
        "application repo checkout %s has no local HEAD and no remote default branch; skipping pull",
        git.source_root,
    )


def ensure_application_repo_checkout(business, *, pull: bool = True) -> tuple[Path, GitWrapper]:
    repo_root = get_application_repo_checkout_path(business)
    repo_root.parent.mkdir(parents=True, exist_ok=True)
    git = GitWrapper(repo_root)

    if git.source_exists():
        _refresh_existing_application_repo_checkout(git, pull=pull)
    else:
        git.clone(business.get_application_repo_url())

    return repo_root, git


def _resolve_application_repo_root(
        business,
        *,
        repo_root: str | Path | None = None,
        pull: bool = False,
) -> tuple[Path, GitWrapper | None]:
    if repo_root is not None:
        resolved_root = Path(repo_root)
        resolved_root.mkdir(parents=True, exist_ok=True)
        git = GitWrapper(resolved_root) if (resolved_root / ".git").exists() else None
        if git is not None:
            _refresh_existing_application_repo_checkout(git, pull=pull)
        return resolved_root, git

    return ensure_application_repo_checkout(business, pull=pull)


def _runtime_revision_config_name(business) -> str:
    return f"{APPLICATION_REPO_SYNC_REVISION_PREFIX}:{business.id}"


def _current_repo_revision(git: GitWrapper | None) -> str | None:
    if git is None or not git.source_exists() or not git.has_head():
        return None
    return git.get_current_revision()


def _normalize_iso_datetime(raw_value: str | None) -> datetime | None:
    normalized_value = common.default_str(raw_value).strip()
    if not normalized_value:
        return None
    return datetime.fromisoformat(normalized_value)


def _task_runtime_payload_from_config(
        task_config: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None]:
    runtime_config = copy.deepcopy(task_config.get("runtime") or {})
    entrypoint_path = normalize_task_bundle_path(
        runtime_config.get("entrypoint_path"),
        allow_none=True,
    )
    prompt_path = normalize_task_bundle_path(
        runtime_config.get("prompt_path"),
        allow_none=True,
    )
    output_schema_path = normalize_task_bundle_path(
        runtime_config.get("output_schema_path"),
        allow_none=True,
    )
    source_kind = common.default_str(runtime_config.get("source_kind")).strip() or None

    if entrypoint_path:
        source_kind = TaskImplementationSourceKind.CODE_FILE.value
    elif prompt_path:
        source_kind = source_kind or TaskImplementationSourceKind.LLM_PROMPT.value

    return source_kind, entrypoint_path, prompt_path, output_schema_path


def _task_source_metadata_from_config(task_config: dict[str, Any]) -> dict[str, Any]:
    task_id = common.assert_not_empty(common.default_str(task_config.get("task_id")).strip())
    runtime_config = copy.deepcopy(task_config.get("runtime") or {})
    source_kind, entrypoint_path, prompt_path, output_schema_path = (
        _task_runtime_payload_from_config(task_config)
    )

    source_metadata = {
        "task_directory": build_task_directory_relative_path(task_id),
        "task_config_path": build_task_config_relative_path(task_id),
        "entrypoint_path": entrypoint_path,
        "prompt_path": prompt_path,
        "output_schema_file_path": output_schema_path,
    }
    llm_config = copy.deepcopy(runtime_config.get("llm") or {})
    for field_name in ("model", "reasoning_effort", "verbosity", "creativity"):
        field_value = common.default_str(llm_config.get(field_name)).strip()
        if field_value:
            source_metadata[field_name] = field_value
    if source_kind:
        source_metadata["source_kind"] = source_kind

    return source_metadata


def _task_evaluator_config_from_config(task_config: dict[str, Any]) -> dict[str, Any]:
    evaluator_config = copy.deepcopy(task_config.get("evaluator") or {})
    evaluator_kind = common.default_str(evaluator_config.get("kind")).strip() or "default"
    evaluator_file_path = normalize_task_bundle_path(
        evaluator_config.get("file_path"),
        allow_none=True,
    )
    evaluator_output_schema_path = normalize_task_bundle_path(
        evaluator_config.get("output_schema_path"),
        allow_none=True,
    )
    if evaluator_kind == "default" or not evaluator_file_path:
        return {}

    task_id = common.assert_not_empty(common.default_str(task_config.get("task_id")).strip())
    task_directory = build_task_directory_relative_path(task_id)
    normalized_config = {
        "kind": evaluator_kind,
        "application_repo_file_path": f"{task_directory}/{evaluator_file_path}",
        "application_repo_ref": common.default_str(
            common.get(task_config, ["runtime", "application_repo_ref"])
        ).strip() or DEFAULT_APPLICATION_REPO_REF,
    }
    if evaluator_output_schema_path:
        normalized_config["output_schema_file_path"] = (
            f"{task_directory}/{evaluator_output_schema_path}"
        )
    for field_name in ("model", "reasoning_effort", "verbosity", "creativity"):
        field_value = common.default_str(evaluator_config.get(field_name)).strip()
        if field_value:
            normalized_config[field_name] = field_value
    return normalized_config


def _task_version_matches_config(version, task_config: dict[str, Any]) -> bool:
    source_kind, _, _, _ = _task_runtime_payload_from_config(task_config)
    if not source_kind:
        return False

    runtime_ref = common.default_str(
        common.get(task_config, ["runtime", "application_repo_ref"])
    ).strip() or DEFAULT_APPLICATION_REPO_REF

    return (
        version.source_kind == source_kind
        and common.default_str(version.application_repo_file_path).strip()
        == build_task_config_relative_path(task_config["task_id"])
        and common.default_str(version.application_repo_ref).strip() == runtime_ref
        and copy.deepcopy(version.source_metadata or {})
        == _task_source_metadata_from_config(task_config)
        and copy.deepcopy(version.evaluator_config or {})
        == _task_evaluator_config_from_config(task_config)
    )


def _upsert_task_scalar_fields(task_config: dict[str, Any]):
    from erieiron_autonomous_agent.models import Initiative, Task

    task_id = common.assert_not_empty(common.default_str(task_config.get("task_id")).strip())
    initiative_id = common.assert_not_empty(
        common.default_str(task_config.get("initiative_id")).strip()
    )
    initiative = Initiative.objects.get(id=initiative_id)

    defaults = {
        "initiative": initiative,
        "description": common.default_str(task_config.get("description")).strip(),
        "status": common.default_str(task_config.get("status")).strip(),
        "task_type": common.default_str(task_config.get("task_type")).strip(),
        "risk_notes": common.default_str(task_config.get("risk_notes")).strip(),
        "completion_criteria": copy.deepcopy(
            common.ensure_list(task_config.get("completion_criteria"))
        ),
        "comment_requests": copy.deepcopy(
            common.ensure_list(task_config.get("comment_requests"))
        ),
        "attachments": copy.deepcopy(common.ensure_list(task_config.get("attachments"))),
        "created_by": common.default_str(task_config.get("created_by")).strip() or None,
        "input_fields": copy.deepcopy(task_config.get("input_fields") or {}),
        "output_fields": copy.deepcopy(common.ensure_list(task_config.get("output_fields"))),
        "requires_test": bool(task_config.get("requires_test")),
        "execution_schedule": common.default_str(
            task_config.get("execution_schedule")
        ).strip(),
        "prompt_improvement_schedule": common.default_str(
            task_config.get("prompt_improvement_schedule")
        ).strip(),
        "execution_start_time": _normalize_iso_datetime(
            task_config.get("execution_start_time")
        ),
        "timeout_seconds": task_config.get("timeout_seconds"),
        "guidance": common.default_str(task_config.get("guidance")).strip() or None,
        "max_budget_usd": task_config.get("max_budget_usd"),
        "implementation_phase": common.default_str(
            task_config.get("implementation_phase")
        ).strip() or None,
    }
    task, _ = Task.objects.update_or_create(id=task_id, defaults=defaults)
    return task


def _sync_task_relationships_and_runtime(task, task_config: dict[str, Any]) -> None:
    from erieiron_autonomous_agent.models import ProductRequirement, Task

    dependency_ids = sorted(
        common.default_str(item).strip()
        for item in common.ensure_list(task_config.get("depends_on"))
        if common.default_str(item).strip()
    )
    dependencies = list(Task.objects.filter(id__in=dependency_ids).order_by("id"))
    resolved_dependency_ids = [dependency.id for dependency in dependencies]
    if resolved_dependency_ids != dependency_ids:
        missing_dependency_ids = sorted(set(dependency_ids) - set(resolved_dependency_ids))
        raise ValidationError(
            f"task {task.id} references missing dependencies: {missing_dependency_ids}"
        )
    task.depends_on.set(dependencies)

    requirement_ids = sorted(
        common.default_str(item).strip()
        for item in common.ensure_list(task_config.get("validated_requirements"))
        if common.default_str(item).strip()
    )
    requirements = list(
        ProductRequirement.objects.filter(id__in=requirement_ids).order_by("id")
    )
    resolved_requirement_ids = [requirement.id for requirement in requirements]
    if resolved_requirement_ids != requirement_ids:
        missing_requirement_ids = sorted(
            set(requirement_ids) - set(resolved_requirement_ids)
        )
        raise ValidationError(
            f"task {task.id} references missing requirements: {missing_requirement_ids}"
        )
    task.validated_requirements.set(requirements)

    source_kind, _, _, _ = _task_runtime_payload_from_config(task_config)
    if not source_kind:
        return

    runtime_ref = common.default_str(
        common.get(task_config, ["runtime", "application_repo_ref"])
    ).strip() or DEFAULT_APPLICATION_REPO_REF
    active_version = task.get_active_implementation_version()
    if active_version and _task_version_matches_config(active_version, task_config):
        return

    task.create_implementation_version(
        source_kind=source_kind,
        application_repo_file_path=build_task_config_relative_path(task.id),
        application_repo_ref=runtime_ref,
        source_metadata=_task_source_metadata_from_config(task_config),
        evaluator_config=_task_evaluator_config_from_config(task_config),
        set_active=True,
    )


def _sync_tasks_from_application_repo(repo_root: Path) -> list[str]:
    task_config_paths = sorted((repo_root / TASKS_ROOT).glob(f"*/{CONFIG_FILENAME}"))
    synced_task_ids: list[str] = []
    task_configs = []
    for config_path in task_config_paths:
        task_config = common.read_json(config_path, default={}) or {}
        task = _upsert_task_scalar_fields(task_config)
        task_configs.append((task, task_config))
        synced_task_ids.append(task.id)

    for task, task_config in task_configs:
        _sync_task_relationships_and_runtime(task, task_config)

    return synced_task_ids


def _sync_workflow_from_config(workflow_config: dict[str, Any]):
    from erieiron_autonomous_agent.models import (
        WorkflowConnection,
        WorkflowDefinition,
        WorkflowStep,
        WorkflowTrigger,
    )

    workflow_id = common.assert_not_empty(
        common.default_str(workflow_config.get("workflow_id")).strip()
    )
    workflow_name = common.default_str(workflow_config.get("name")).strip()
    WorkflowDefinition.objects.exclude(id=workflow_id).filter(name=workflow_name).delete()
    workflow, _ = WorkflowDefinition.objects.update_or_create(
        id=workflow_id,
        defaults={
            "name": workflow_name,
            "description": common.default_str(workflow_config.get("description")).strip()
            or None,
            "source_kind": WorkflowDefinitionSourceKind.APPLICATION_REPO.value,
            "is_active": bool(workflow_config.get("is_active", True)),
        },
    )

    step_ids = []
    for step_config in workflow_config.get("steps") or []:
        step_id = common.assert_not_empty(common.default_str(step_config.get("id")).strip())
        WorkflowStep.objects.update_or_create(
            id=step_id,
            defaults={
                "workflow": workflow,
                "name": common.default_str(step_config.get("name")).strip(),
                "handler_path": common.default_str(
                    step_config.get("handler_path")
                ).strip(),
                "emits_message_type": common.default_str(
                    step_config.get("emits_message_type")
                ).strip()
                or None,
                "sort_order": int(step_config.get("sort_order") or 0),
            },
        )
        step_ids.append(step_id)
    workflow.steps.exclude(id__in=step_ids).delete()

    step_map = {str(step.id): step for step in workflow.steps.all()}

    trigger_ids = []
    for trigger_config in workflow_config.get("triggers") or []:
        trigger_id = common.assert_not_empty(
            common.default_str(trigger_config.get("id")).strip()
        )
        target_step_id = common.assert_not_empty(
            common.default_str(trigger_config.get("target_step_id")).strip()
        )
        WorkflowTrigger.objects.update_or_create(
            id=trigger_id,
            defaults={
                "workflow": workflow,
                "target_step": step_map[target_step_id],
                "message_type": common.default_str(
                    trigger_config.get("message_type")
                ).strip(),
                "sort_order": int(trigger_config.get("sort_order") or 0),
            },
        )
        trigger_ids.append(trigger_id)
    workflow.triggers.exclude(id__in=trigger_ids).delete()

    connection_ids = []
    for connection_config in workflow_config.get("connections") or []:
        connection_id = common.assert_not_empty(
            common.default_str(connection_config.get("id")).strip()
        )
        source_step_id = common.assert_not_empty(
            common.default_str(connection_config.get("source_step_id")).strip()
        )
        target_step_id = common.assert_not_empty(
            common.default_str(connection_config.get("target_step_id")).strip()
        )
        WorkflowConnection.objects.update_or_create(
            id=connection_id,
            defaults={
                "workflow": workflow,
                "source_step": step_map[source_step_id],
                "target_step": step_map[target_step_id],
                "message_type": common.default_str(
                    connection_config.get("message_type")
                ).strip(),
                "sort_order": int(connection_config.get("sort_order") or 0),
            },
        )
        connection_ids.append(connection_id)
    workflow.connections.exclude(id__in=connection_ids).delete()

    return workflow


def _sync_workflows_from_application_repo(
        repo_root: Path,
        *,
        prune_missing: bool = True,
) -> list[str]:
    from erieiron_autonomous_agent.models import WorkflowDefinition

    workflow_config_paths = sorted((repo_root / WORKFLOWS_ROOT).glob(f"*/{CONFIG_FILENAME}"))
    synced_workflow_ids: list[str] = []
    for config_path in workflow_config_paths:
        workflow_config = common.read_json(config_path, default={}) or {}
        workflow = _sync_workflow_from_config(workflow_config)
        synced_workflow_ids.append(str(workflow.id))

    if prune_missing and synced_workflow_ids:
        WorkflowDefinition.objects.filter(
            source_kind=WorkflowDefinitionSourceKind.APPLICATION_REPO.value
        ).exclude(id__in=synced_workflow_ids).delete()

    return synced_workflow_ids


def sync_business_application_repo(
        business,
        *,
        repo_root: str | Path | None = None,
        pull: bool = True,
        force: bool = False,
) -> dict[str, Any]:
    resolved_repo_root, git = _resolve_application_repo_root(
        business,
        repo_root=repo_root,
        pull=pull,
    )
    revision = _current_repo_revision(git)
    revision_config_name = _runtime_revision_config_name(business)
    previous_revision = RuntimeConfig.instance().get(revision_config_name)
    if not force and revision and previous_revision == revision:
        return {
            "repo_root": resolved_repo_root,
            "revision": revision,
            "changed": False,
            "workflow_ids": [],
            "task_ids": [],
        }

    with transaction.atomic():
        workflow_ids = _sync_workflows_from_application_repo(resolved_repo_root)
        task_ids = _sync_tasks_from_application_repo(resolved_repo_root)

    if revision:
        RuntimeConfig.instance().set_value(revision_config_name, revision)

    return {
        "repo_root": resolved_repo_root,
        "revision": revision,
        "changed": True,
        "workflow_ids": workflow_ids,
        "task_ids": task_ids,
    }


def sync_business_application_repo_if_changed(
        business,
        *,
        repo_root: str | Path | None = None,
) -> dict[str, Any]:
    return sync_business_application_repo(
        business,
        repo_root=repo_root,
        pull=True,
        force=False,
    )


def sync_erieiron_application_repo_if_changed(
        *,
        repo_root: str | Path | None = None,
) -> dict[str, Any]:
    from erieiron_autonomous_agent.models import Business

    return sync_business_application_repo_if_changed(
        Business.get_erie_iron_business(),
        repo_root=repo_root,
    )
