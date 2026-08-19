"""
Sandbox — permission system for controlling command execution.

The sandbox determines whether a command can be executed automatically,
requires user confirmation, or should be blocked entirely.

It works in conjunction with the Soul configuration to enforce
behavioral rules defined in soul.yaml.

Usage::

    sandbox = Sandbox(soul)

    result = sandbox.check_command("ls -la")
    if result.allowed:
        ...
    elif result.requires_confirmation:
        # Ask user for confirmation
        await channel.send_message(user_id, result.message)
"""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

import structlog

from harness.core.exceptions import BOUNDARY_ERRORS

if TYPE_CHECKING:
    from harness.soul.loader import Soul

log = structlog.get_logger(__name__)


class PermissionLevel(Enum):
    """Permission levels for command execution."""

    ALLOWED = "allowed"  # Safe to execute automatically
    REQUIRES_CONFIRMATION = "confirm"  # Needs user approval first
    BLOCKED = "blocked"  # Never execute (e.g., fork bombs)


@dataclass
class PermissionResult:
    """Result of a permission check."""

    level: PermissionLevel
    allowed: bool
    requires_confirmation: bool
    message: str | None = None

    @classmethod
    def allow(cls) -> PermissionResult:
        """Command is safe to execute."""
        return cls(
            level=PermissionLevel.ALLOWED,
            allowed=True,
            requires_confirmation=False,
        )

    @classmethod
    def confirm(cls, message: str) -> PermissionResult:
        """Command requires user confirmation."""
        return cls(
            level=PermissionLevel.REQUIRES_CONFIRMATION,
            allowed=False,
            requires_confirmation=True,
            message=message,
        )

    @classmethod
    def block(cls, message: str) -> PermissionResult:
        """Command is blocked entirely."""
        return cls(
            level=PermissionLevel.BLOCKED,
            allowed=False,
            requires_confirmation=False,
            message=message,
        )


@dataclass
class CommandResult:
    """Result of a command execution."""

    stdout: str
    stderr: str
    return_code: int
    command: str

    @property
    def success(self) -> bool:
        return self.return_code == 0

    @property
    def output(self) -> str:
        """Combined stdout and stderr."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(f"[stderr] {self.stderr}")
        return "\n".join(parts) if parts else "(no output)"


class Sandbox:
    """
    Permission system for command execution.

    Uses the Soul configuration to determine what commands
    are safe, require confirmation, or should be blocked.
    """

    # Commands that are ALWAYS blocked, regardless of Soul config
    # These are extremely dangerous and have no legitimate use case
    # Note: "rm -rf /tmp/something" is NOT blocked - it requires confirmation via Soul rules
    ALWAYS_BLOCKED: ClassVar[list[str]] = [
        ":(){ :|:& };:",  # Fork bomb
        "rm -rf /",  # Delete entire filesystem
        "rm -rf /*",  # Delete entire filesystem
        "rm -rf / --no-preserve-root",
        "dd if=/dev/zero of=/dev/sda",
        "dd if=/dev/random of=/dev/sda",
        "mkfs.ext4 /dev/sda",
        "mkfs.ext3 /dev/sda",
        "> /dev/sda",
        "chmod -R 777 /",
        "chown -R nobody /",
        "mv / /dev/null",
        "wget -O- http | sh",  # Piping unknown scripts
        "curl http | sh",
    ]

    def __init__(self, soul: Soul) -> None:
        self._soul = soul

    def check_command(self, command: str) -> PermissionResult:
        """
        Check if a command is allowed to execute.

        :param command: The shell command to check.
        :returns: PermissionResult with the decision.
        """
        command = command.strip()

        if self._is_always_blocked(command):
            log.warning("command_blocked", command=command)
            return PermissionResult.block(
                f"⛔ Comando bloqueado por segurança: `{command}`"
            )

        if self._soul.requires_confirmation(command):
            log.info("command_requires_confirmation", command=command)
            return PermissionResult.confirm(
                f"⚠️ Este comando requer confirmação:\n```\n{command}\n```\n"
                f"Deseja executar? Responda 'sim' para confirmar."
            )

        if self._soul.is_auto_approved(command):
            log.debug("command_auto_approved", command=command)
            return PermissionResult.allow()

        # Unknown command — ask for confirmation to be safe
        log.info("command_unknown_asking_confirmation", command=command)
        return PermissionResult.confirm(
            f"🤔 Não tenho certeza se este comando é seguro:\n```\n{command}\n```\n"
            f"Deseja executar? Responda 'sim' para confirmar."
        )

    def _is_always_blocked(self, command: str) -> bool:
        """Check if command matches any always-blocked pattern."""
        command_lower = command.lower().strip()

        for blocked in self.ALWAYS_BLOCKED:
            blocked_lower = blocked.lower()

            # Exact match
            if command_lower == blocked_lower:
                return True

            # For "rm -rf /" and "rm -rf /*", we need exact match or match with flags
            # "rm -rf /tmp/test" should NOT match "rm -rf /"
            if blocked_lower in (
                "rm -rf /",
                "rm -rf /*",
                "rm -rf / --no-preserve-root",
            ):
                # Only block if it's exactly these or starts with them and doesn't have a path
                if command_lower == blocked_lower:
                    return True
                # "rm -rf / --something" should be blocked
                if command_lower.startswith(("rm -rf / ", "rm -rf /* ")):
                    return True
                continue

            # For other patterns, check if command starts with the blocked pattern
            # This handles things like ":(){ :|:& };:" appearing anywhere
            if blocked_lower in command_lower:
                return True

        return False

    async def execute(
        self,
        command: str,
        timeout: float = 30.0,
        cwd: str | None = None,
    ) -> CommandResult:
        """
        Execute a command and return the result.

        This method does NOT check permissions — call check_command() first.

        :param command: The shell command to execute.
        :param timeout: Maximum execution time in seconds.
        :param cwd: Working directory for the command.
        :returns: CommandResult with stdout, stderr, and return code.
        """
        log.info("executing_command", command=command, cwd=cwd, timeout=timeout)

        try:
            # Use asyncio subprocess for non-blocking execution
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except TimeoutError:
                process.kill()
                await process.communicate()
                return CommandResult(
                    stdout="",
                    stderr=f"Command timed out after {timeout}s",
                    return_code=-1,
                    command=command,
                )

            result = CommandResult(
                stdout=stdout.decode("utf-8", errors="replace").strip(),
                stderr=stderr.decode("utf-8", errors="replace").strip(),
                return_code=process.returncode or 0,
                command=command,
            )

            log.info(
                "command_executed",
                command=command,
                return_code=result.return_code,
                stdout_len=len(result.stdout),
                stderr_len=len(result.stderr),
            )

            return result

        except BOUNDARY_ERRORS as exc:
            log.error("command_execution_failed", command=command, error=str(exc))
            return CommandResult(
                stdout="",
                stderr=f"Execution failed: {exc}",
                return_code=-1,
                command=command,
            )

    def execute_sync(
        self,
        command: str,
        timeout: float = 30.0,
        cwd: str | None = None,
    ) -> CommandResult:
        """
        Synchronous version of execute() for use outside async context.
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=timeout,
                cwd=cwd,
                check=False,
            )

            return CommandResult(
                stdout=result.stdout.decode("utf-8", errors="replace").strip(),
                stderr=result.stderr.decode("utf-8", errors="replace").strip(),
                return_code=result.returncode,
                command=command,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                stdout="",
                stderr=f"Command timed out after {timeout}s",
                return_code=-1,
                command=command,
            )
        except BOUNDARY_ERRORS as exc:
            return CommandResult(
                stdout="",
                stderr=f"Execution failed: {exc}",
                return_code=-1,
                command=command,
            )
