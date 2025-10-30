"""Helpers for collecting and structuring OpenTofu deployment logs."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from erieiron_common import common

LOGGER = logging.getLogger(__name__)


@dataclass
class OpenTofuRunResult:
    """Captures the stdout/stderr streams for a tofu CLI invocation."""

    stage: str
    command: Sequence[str]
    returncode: int
    started_at: datetime
    completed_at: datetime
    stdout: str
    stderr: str
    extra: Mapping[str, Any] | None = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "command": " ".join(self.command),
            "returncode": self.returncode,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "duration_seconds": max((self.completed_at - self.started_at).total_seconds(), 0.0),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "extra": self.extra or {},
        }


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


def build_opentofu_log_payload(
        *,
        stack_type: str,
        plan_summary: Mapping[str, Any] | None,
        results: Sequence[Mapping[str, Any]],
        outputs: Mapping[str, Any] | None,
        tfvars: Mapping[str, Any] | None,
        error: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "stack_type": stack_type,
        "plan_summary": plan_summary or {},
        "plan_results": list(results),
        "outputs": outputs or {},
        "tfvars": tfvars or {},
        "error": error or {},
    }
    return payload


def write_log_payload(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str))


__all__ = [
    "OpenTofuRunResult",
    "build_opentofu_log_payload",
    "summarize_plan_changes",
    "write_log_payload",
]
