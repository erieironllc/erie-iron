"""Utilities for working with OpenTofu (Terraform) configurations."""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from erieiron_common.enums import InfrastructureStackType


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
