from __future__ import annotations

import logging
import os
import subprocess
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, TYPE_CHECKING

from erieiron_common import common
from erieiron_common.enums import (
    LlmCreativity,
    LlmReasoningEffort,
    LlmVerbosity,
)
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.llm_apis.llm_response import LlmResponse

from erieiron_autonomous_agent.system_agent_llm_interface import get_sys_prompt, llm_chat

if TYPE_CHECKING:  # pragma: no cover - typing helper only
    from .coding_agent_config import CodingAgentConfig


def _truncate(value: str, limit: int = 4000) -> str:
    if not value:
        return ""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n... [truncated {len(value) - limit} chars]"


@dataclass
class CommandResult:
    label: str
    shell_command: Sequence[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "command": " ".join(self.shell_command),
            "returncode": self.returncode,
            "duration_seconds": self.duration_seconds,
            "stdout": _truncate(self.stdout or ""),
            "stderr": _truncate(self.stderr or ""),
            "metadata": self.metadata,
        }


@dataclass
class CommandSpec:
    label: str
    shell_command: Sequence[str]
    workdir: Path | None = None
    env: Mapping[str, str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    action: Callable[["CodingAgentConfig", "CommandSpec"], CommandResult] | None = None

    def execute(self, config: "CodingAgentConfig") -> CommandResult:
        start = time.time()
        shell_preview = " ".join(self.shell_command)
        config.log(f"[Frontier] Starting command '{self.label}': {shell_preview}")
        try:
            if self.action:
                result = self.action(config, self)
                if not isinstance(result, CommandResult):
                    raise ValueError(f"Command '{self.label}' action must return CommandResult")
            else:
                result = self._run_shell(config)
        except Exception as exc:  # noqa: BLE001 - propagate failure info in result
            logging.exception(exc)
            stderr = common.get_stack_trace_as_string(exc)
            result = CommandResult(
                label=self.label,
                shell_command=list(self.shell_command),
                returncode=1,
                stdout="",
                stderr=stderr,
                metadata={**self.metadata, "exception": type(exc).__name__},
                duration_seconds=time.time() - start,
            )
        config.log(f"[Frontier] Completed '{self.label}' with code {result.returncode}")
        return result

    def _run_shell(self, config: "CodingAgentConfig") -> CommandResult:
        start = time.time()
        env = os.environ.copy()
        env.update(config.runtime_env)
        if self.env:
            env.update(self.env)
        cwd = str(self.workdir or config.sandbox_root_dir)
        process = subprocess.Popen(  # noqa: S603
            list(self.shell_command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
            env=env,
        )
        stdout_parts: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            config.log(line.rstrip("\n"))
            stdout_parts.append(line)
        process.wait()
        stdout_text = "".join(stdout_parts)
        stderr_text = "" if process.returncode == 0 else stdout_text
        return CommandResult(
            label=self.label,
            shell_command=list(self.shell_command),
            returncode=process.returncode,
            stdout=stdout_text,
            stderr=stderr_text,
            metadata=self.metadata,
            duration_seconds=time.time() - start,
        )


@dataclass
class CommandPlan:
    description: str
    commands: list[CommandSpec] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "context": self.context,
            "commands": [
                {
                    "label": spec.label,
                    "command": " ".join(spec.shell_command),
                    "metadata": spec.metadata,
                }
                for spec in self.commands
            ],
        }

    def extend(self, specs: Iterable[CommandSpec]) -> None:
        self.commands.extend(list(specs))

    def is_empty(self) -> bool:
        return not self.commands


class FrontierSession:
    def __init__(self, config: "CodingAgentConfig"):
        self.config = config
        self.history: list[dict[str, Any]] = []
        self.metrics: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    def plan_changes(self, planning_messages: Sequence[LlmMessage]) -> dict:
        messages = [
            get_sys_prompt("frontier_session_plan_changes.md"),
            LlmMessage.user_from_data("Task Context", self._build_task_context()),
            *self._history_messages(),
            *planning_messages,
        ]
        response = llm_chat(
            "Frontier Session - Plan Changes",
            messages,
            tag_entity=self.config.current_iteration,
            output_schema="frontier_session_plan_changes.md.schema.json",
            reasoning_effort=LlmReasoningEffort.MEDIUM,
            verbosity=LlmVerbosity.MEDIUM,
            creativity=LlmCreativity.LOW,
        )
        data = response.json()
        data["_llm_model"] = getattr(response.model, "value", response.model)
        self._record_call_metrics("plan_changes", response)
        self._append_history("plan_changes", {
            "objective": common.get(data, ["implementation_directive", "objective"]),
            "summary": common.get(data, ["implementation_directive", "high_level_approach"]),
        })
        return data

    def run_commands(self, plan: CommandPlan) -> dict[str, Any]:
        if plan.is_empty():
            return {"status": "skipped", "analysis": {}, "command_results": []}
        command_results: list[CommandResult] = []
        for spec in plan.commands:
            result = spec.execute(self.config)
            command_results.append(result)
        payload_results = [result.to_payload() for result in command_results]
        messages = [
            get_sys_prompt("frontier_session_run_commands.md"),
            LlmMessage.user_from_data("Task Context", self._build_task_context()),
            LlmMessage.user_from_data("Command Plan", plan.to_payload()),
            LlmMessage.user_from_data("Command Execution Results", payload_results),
            *self._history_messages(),
        ]
        response = llm_chat(
            "Frontier Session - Command Execution",
            messages,
            tag_entity=self.config.current_iteration,
            output_schema="frontier_session_run_commands.md.schema.json",
            reasoning_effort=LlmReasoningEffort.LOW,
            verbosity=LlmVerbosity.LOW,
            creativity=LlmCreativity.NONE,
        )
        data = response.json()
        self._record_call_metrics("run_commands", response)
        self._append_history("run_commands", {"status": data.get("status")})
        return {
            "status": data.get("status"),
            "analysis": data,
            "command_results": command_results,
        }

    def summarize_iteration(self, iteration_context: Mapping[str, Any]) -> dict:
        messages = [
            get_sys_prompt("frontier_session_summarize_iteration.md"),
            LlmMessage.user_from_data("Task Context", self._build_task_context()),
            LlmMessage.user_from_data("Iteration Evaluation Inputs", dict(iteration_context)),
            *self._history_messages(),
        ]
        response = llm_chat(
            "Frontier Session - Summarize Iteration",
            messages,
            tag_entity=self.config.current_iteration,
            output_schema="frontier_session_summarize_iteration.md.schema.json",
            reasoning_effort=LlmReasoningEffort.MEDIUM,
            verbosity=LlmVerbosity.MEDIUM,
            creativity=LlmCreativity.LOW,
        )
        data = response.json()
        self._record_call_metrics("summarize_iteration", response)
        self._append_history("summarize_iteration", {
            "goal_achieved": data.get("goal_achieved"),
            "blocked": data.get("blocked"),
        })
        return data

    def patch_iac(self, iac_context: Mapping[str, Any]) -> dict:
        messages = [
            get_sys_prompt("frontier_session_patch_iac.md"),
            LlmMessage.user_from_data("Task Context", self._build_task_context()),
            LlmMessage.user_from_data("IaC Context", dict(iac_context)),
            *self._history_messages(),
        ]
        response = llm_chat(
            "Frontier Session - IaC Guidance",
            messages,
            tag_entity=self.config.current_iteration,
            output_schema="frontier_session_patch_iac.md.schema.json",
            reasoning_effort=LlmReasoningEffort.MEDIUM,
            verbosity=LlmVerbosity.MEDIUM,
            creativity=LlmCreativity.LOW,
        )
        data = response.json()
        self._record_call_metrics("patch_iac", response)
        self._append_history("patch_iac", {"action": data.get("recommended_action")})
        return data

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _build_task_context(self) -> dict[str, Any]:
        config = self.config
        task = config.task
        business = config.business
        iteration = config.current_iteration
        readonly_files = config.self_driving_task.get_readonly_files()
        return {
            "task_id": task.id,
            "task_type": task.task_type,
            "iteration_id": getattr(iteration, "id", None),
            "iteration_version": getattr(iteration, "version_number", None),
            "business": business.service_token,
            "initiative": config.initiative.title,
            "ui_first_phase": config.is_ui_first_phase,
            "env_type": config.env_type.value,
            "readonly_files": readonly_files,
        }

    def _history_messages(self) -> list[LlmMessage]:
        if not self.history:
            return []
        recent = self.history[-5:]
        return [
            LlmMessage.user_from_data(
                "Frontier Session History",
                recent,
            )
        ]

    def _record_call_metrics(self, action: str, response: LlmResponse) -> None:
        metrics = {
            "action": action,
            "llm_request_id": response.llm_request_id,
            "tokens": response.token_count,
            "cost_usd": response.price_total,
            "chat_millis": response.chat_millis,
        }
        self.metrics.append(metrics)
        self.config.log(
            f"[Frontier] {action} tokens={metrics['tokens']} cost=${metrics['cost_usd']:.4f}"
        )

    def _append_history(self, action: str, payload: Mapping[str, Any]) -> None:
        entry = {
            "action": action,
            "payload": dict(payload),
            "timestamp": common.get_now().isoformat(),
        }
        self.history.append(entry)
