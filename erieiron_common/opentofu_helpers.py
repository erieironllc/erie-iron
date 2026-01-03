"""Utilities for working with OpenTofu (Terraform) configurations."""
from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from erieiron_common.date_utils import to_utc
from erieiron_common.enums import OpenTofuErrorType

KNOWN_DUPLICATE_MSGS = {
    "InvalidPermission.Duplicate",
    "InvalidChangeBatch",
    "AlreadyExists",
}

HARD_ERROR_INDICATORS = {
    "Error: creating",
    "Error: updating",
}

# Permissions-related error patterns that indicate missing AWS permissions
PERMISSIONS_ERROR_PATTERNS = [
    "AccessDenied",
    "UnauthorizedOperation",
    "Forbidden",
    "StatusCode: 403",
    "is not authorized to perform",
    "User: arn:aws:sts::",
    "does not have permission to perform",
    "InsufficientCapabilitiesException",
]


class OpenTofuException(Exception):
    """Base exception for OpenTofu helper errors."""


class OpenTofuCommandException(OpenTofuException):
    """Raised when an OpenTofu CLI invocation fails."""
    
    def __init__(self, message: str, result: "OpenTofuCommandResult"):
        super().__init__(message)
        self.result = result
    
    def is_state_lock_error(self) -> bool:
        """Check if the error is a state lock error."""
        stderr = self.result.stderr or ""
        return "Error acquiring the state lock" in stderr
    
    def extract_lock_id(self) -> str | None:
        """Extract the lock ID from a state lock error message."""
        stderr = self.result.stderr or ""
        match = re.search(r'ID:\s+([a-f0-9-]+)', stderr)
        return match.group(1) if match else None
    
    def log_error(self):
        # Enhanced error logging and context
        logging.error(f"[DEPLOY_DEBUG] OpenTofu command failed")
        logging.error(f"[DEPLOY_DEBUG] Command: {' '.join(self.result.command)}")
        logging.error(f"[DEPLOY_DEBUG] Exit code: {self.result.returncode}")
        logging.error(f"[DEPLOY_DEBUG] Stdout: {self.result.stdout}")
        logging.error(f"[DEPLOY_DEBUG] Stderr: {self.result.stderr}")
    
    def extract_lock_created_time(self) -> datetime | None:
        """Extract the lock creation time from error message."""
        stderr = self.result.stderr or ""
        match = re.search(r'Created:\s+([0-9-]+\s+[0-9:.]+\s+\+0000\s+UTC)', stderr)
        if match:
            time_str = match.group(1)
            try:
                # Parse format like "2025-12-14 18:53:56.668832 +0000 UTC"
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S.%f %z %Z")
                return to_utc(dt)
            except ValueError:
                try:
                    # Try without microseconds
                    dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S %z %Z")
                    return to_utc(dt)
                except ValueError:
                    return None
        return None
    
    def extract_error_payload(self):
        error_payload = {}
        
        # Parse stderr for common permission error patterns and provide troubleshooting hints
        troubleshooting_hints = []
        stderr_lower = self.result.stderr.lower() if self.result.stderr else ""
        
        if "bucket" in stderr_lower and "exist" in stderr_lower:
            troubleshooting_hints.append("S3_ISSUE: Check if S3 bucket exists and is accessible")
        
        if "dynamodb" in stderr_lower and "lock" in stderr_lower:
            troubleshooting_hints.append("LOCK_ISSUE: Check DynamoDB table for OpenTofu state locking")
        
        # [DEPLOY_DEBUG] Enhanced DNS/networking error detection
        if "no such host" in stderr_lower or "dial tcp" in stderr_lower:
            troubleshooting_hints.append("DNS_ISSUE: Cannot resolve AWS service endpoints - check DNS configuration")
        
        if "ecs" in stderr_lower and ("create" in stderr_lower or "cluster" in stderr_lower):
            troubleshooting_hints.append("ECS_SPECIFIC: ECS cluster creation failed - may be VPC/network configuration issue")
        
        error_payload = {
            "message": str(self),
            "stderr": self.result.stderr,
            "stdout": self.result.stdout,
            "command": " ".join(self.result.command),
            "troubleshooting_hints": troubleshooting_hints,
        }
        return error_payload
    
    def classify_apply_error(self) -> OpenTofuErrorType:
        """Classify an apply command error for appropriate handling strategy."""
        stderr = self.result.stderr or ""
        stdout = self.result.stdout or ""
        combined = f"{stderr}\n{stdout}"
        
        if (
                "secrets manager" in combined.lower()
                and "scheduled for deletion" in combined.lower()
                and "create this secret" in combined.lower()
        ):
            return OpenTofuErrorType.SECRET_NEEDS_RESTORATION

        # Check for permissions-related errors
        if any(pattern in combined for pattern in PERMISSIONS_ERROR_PATTERNS):
            return OpenTofuErrorType.MISSING_PERMISSIONS
        
        if any(indicator in combined for indicator in HARD_ERROR_INDICATORS):
            return OpenTofuErrorType.PERMANENT_ERROR
        
        # Check for known duplicate/idempotent errors
        duplicate_token: str | None = None
        for msg in KNOWN_DUPLICATE_MSGS:
            if msg in combined:
                duplicate_token = msg
                break
        
        if not duplicate_token:
            return OpenTofuErrorType.TRANSIENT_ERROR
        
        if "InvalidPermission.Duplicate" in combined:
            return OpenTofuErrorType.DUPLICATE_IDEMPOTENT
        
        return OpenTofuErrorType.DUPLICATE_IDEMPOTENT
    
    def extract_missing_permissions(self) -> list[str]:
        """Extract specific missing permissions from OpenTofu error output."""
        stderr = self.result.stderr or ""
        stdout = self.result.stdout or ""
        combined = f"{stderr}\n{stdout}"
        
        missing_permissions = []
        
        # Pattern 1: "is not authorized to perform: <action>"
        pattern1 = re.findall(r'is not authorized to perform[:\s]+([a-zA-Z0-9:*_-]+)', combined, re.IGNORECASE)
        missing_permissions.extend(pattern1)
        
        # Pattern 2: "does not have permission to perform: <action>"
        pattern2 = re.findall(r'does not have permission to perform[:\s]+([a-zA-Z0-9:*_-]+)', combined, re.IGNORECASE)
        missing_permissions.extend(pattern2)
        
        # Pattern 3: Direct action mentions in access denied errors
        access_denied_lines = [line for line in combined.split('\n') if 'AccessDenied' in line or 'UnauthorizedOperation' in line]
        for line in access_denied_lines:
            # Extract AWS actions from context (common AWS API patterns)
            # noinspection RegExpUnnecessaryNonCapturingGroup
            aws_actions = re.findall(r'([a-z][a-zA-Z0-9]*:[a-zA-Z][a-zA-Z0-9]*(?:\*)?)', line)
            missing_permissions.extend(aws_actions)
        
        # Pattern 4: CloudFormation capability errors
        if 'InsufficientCapabilitiesException' in combined:
            missing_permissions.append('iam:CreateRole')
            missing_permissions.append('iam:AttachRolePolicy')
            missing_permissions.append('iam:DetachRolePolicy')
            missing_permissions.append('iam:DeleteRole')
        
        # Deduplicate while preserving order
        seen = set()
        unique_permissions = []
        for perm in missing_permissions:
            if perm and perm not in seen:
                unique_permissions.append(perm)
                seen.add(perm)
        
        # Normalize AWS-style capitalization
        def normalize(perm: str) -> str:
            if ":" not in perm:
                return perm
            svc, action = perm.split(":", 1)
            parts = re.split(r'[^a-zA-Z0-9]', action)
            normalized_action = "".join(part.capitalize() for part in parts if part)
            return f"{svc.lower()}:{normalized_action}"
        
        return [normalize(p) for p in unique_permissions]
    
    def get_duplicate_token(self):
        """Handle duplicate error as idempotent success."""
        stderr = self.result.stderr or ""
        stdout = self.result.stdout or ""
        combined = f"{stderr}\n{stdout}"
        
        # Find the duplicate token for logging
        return next(
            (msg for msg in KNOWN_DUPLICATE_MSGS if msg in combined),
            "unknown"
        )


class OpenTofuStackObsolete(OpenTofuException):
    """Raised when a stack must be rotated before continuing."""


class MissingStackPerms(OpenTofuException):
    """Raised when OpenTofu operations fail due to missing AWS permissions."""
    
    def __init__(self, message: str, missing_permissions: list[str], result: "OpenTofuCommandResult"):
        super().__init__(message)
        self.missing_permissions = missing_permissions
        self.result = result


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
