"""Utilities for selecting the active self-driving coder agent implementation."""
from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from typing import Any, Protocol

import settings

_PROVIDER_MODULES = {
    "opentofu": "erieiron_autonomous_agent.coding_agents.self_driving_coder_agent_tofu",
    "cloudformation": "erieiron_autonomous_agent.coding_agents.self_driving_coder_agent",
}


class SelfDrivingAgentProtocol(Protocol):
    """Protocol describing the callable surface we rely on."""

    def execute(self, task_id: str, one_off_action=None, **kwargs: Any) -> None: ...

    def on_reset_task_test(self, task_id: str): ...


@lru_cache(maxsize=1)
def get_self_driving_coder_agent_module() -> SelfDrivingAgentProtocol:
    provider = getattr(settings, "SELF_DRIVING_IAC_PROVIDER", "opentofu").lower()
    module_path = _PROVIDER_MODULES.get(provider)
    if not module_path:
        valid = ", ".join(sorted(_PROVIDER_MODULES))
        raise ValueError(
            f"Unsupported SELF_DRIVING_IAC_PROVIDER '{provider}'. Supported providers: {valid}."
        )
    return import_module(module_path)  # type: ignore[return-value]


def get_provider_name() -> str:
    return getattr(settings, "SELF_DRIVING_IAC_PROVIDER", "opentofu").lower()
