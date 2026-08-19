"""Tests for the Sandbox permission system."""

import pytest

from harness.core.sandbox import (
    CommandResult,
    PermissionLevel,
    PermissionResult,
    Sandbox,
)
from harness.soul.loader import Soul


class TestSandboxPermissions:
    """Tests for command permission classification."""

    @pytest.fixture
    def soul(self) -> Soul:
        """Create a soul with test behaviors."""
        return Soul(
            name="Test",
            require_confirmation_patterns=["rm -rf*", "DROP TABLE*", "docker stop*"],
            auto_approve_patterns=["ls*", "cat*", "pwd*", "echo*", "git status*"],
        )

    @pytest.fixture
    def sandbox(self, soul: Soul) -> Sandbox:
        """Create a sandbox with the test soul."""
        return Sandbox(soul)

    def test_auto_approved_commands(self, sandbox: Sandbox) -> None:
        """Commands in auto_approve list are allowed."""
        result = sandbox.check_command("ls -la")

        assert result.level == PermissionLevel.ALLOWED
        assert result.allowed is True

    def test_requires_confirmation_commands(self, sandbox: Sandbox) -> None:
        """Commands in require_confirmation list need confirmation."""
        result = sandbox.check_command("rm -rf /tmp/test")

        assert result.level == PermissionLevel.REQUIRES_CONFIRMATION
        assert result.allowed is False
        assert result.requires_confirmation is True

    def test_blocked_dangerous_commands(self, sandbox: Sandbox) -> None:
        """Extremely dangerous commands are always blocked."""
        # Root deletion
        result = sandbox.check_command("rm -rf /")
        assert result.level == PermissionLevel.BLOCKED

        # Fork bomb
        result = sandbox.check_command(":(){ :|:& };:")
        assert result.level == PermissionLevel.BLOCKED

    def test_unknown_commands_default_behavior(self, sandbox: Sandbox) -> None:
        """Commands not in any list ask for confirmation."""
        result = sandbox.check_command("curl https://example.com")

        # Unknown commands should require confirmation
        assert result.level == PermissionLevel.REQUIRES_CONFIRMATION
        assert result.requires_confirmation is True


class TestBlockedCommands:
    """Tests for always-blocked dangerous commands."""

    @pytest.fixture
    def sandbox(self) -> Sandbox:
        """Create a sandbox with minimal soul."""
        soul = Soul(
            name="Test",
            require_confirmation_patterns=[],
            auto_approve_patterns=[],
        )
        return Sandbox(soul)

    def test_root_deletion_blocked(self, sandbox: Sandbox) -> None:
        """rm -rf / is always blocked."""
        for cmd in ["rm -rf /", "rm -rf /*", "rm -rf / --no-preserve-root"]:
            result = sandbox.check_command(cmd)
            assert result.level == PermissionLevel.BLOCKED, (
                f"Expected {cmd} to be blocked"
            )

    def test_fork_bomb_blocked(self, sandbox: Sandbox) -> None:
        """Fork bombs are blocked."""
        result = sandbox.check_command(":(){ :|:& };:")
        assert result.level == PermissionLevel.BLOCKED

    def test_disk_wipe_blocked(self, sandbox: Sandbox) -> None:
        """Disk wipe commands are blocked."""
        result = sandbox.check_command("dd if=/dev/zero of=/dev/sda")
        assert result.level == PermissionLevel.BLOCKED


class TestSandboxExecution:
    """Tests for command execution through sandbox."""

    @pytest.fixture
    def sandbox(self) -> Sandbox:
        """Create a sandbox that allows safe commands."""
        soul = Soul(
            name="Test",
            require_confirmation_patterns=["rm*"],
            auto_approve_patterns=["echo*", "pwd*", "ls*", "cat*"],
        )
        return Sandbox(soul)

    @pytest.mark.asyncio
    async def test_execute_echo(self, sandbox: Sandbox) -> None:
        """Execute an echo command."""
        result = await sandbox.execute("echo 'hello world'")

        assert result.success is True
        assert "hello world" in result.stdout
        assert result.return_code == 0

    @pytest.mark.asyncio
    async def test_execute_pwd(self, sandbox: Sandbox) -> None:
        """Execute pwd command."""
        result = await sandbox.execute("pwd")

        assert result.success is True
        assert result.stdout.strip() != ""  # Should return some path

    @pytest.mark.asyncio
    async def test_execute_failing_command(self, sandbox: Sandbox) -> None:
        """Execute a command that fails."""
        result = await sandbox.execute("ls /nonexistent/path/12345")

        assert result.success is False
        assert result.return_code != 0

    @pytest.mark.asyncio
    async def test_execute_with_timeout(self, sandbox: Sandbox) -> None:
        """Command timeout works."""
        result = await sandbox.execute("sleep 10", timeout=0.1)

        assert result.success is False
        assert "timed out" in result.stderr.lower()


class TestCommandResult:
    """Tests for CommandResult dataclass."""

    def test_command_result_success(self) -> None:
        """Successful command result."""
        result = CommandResult(
            stdout="output",
            stderr="",
            return_code=0,
            command="echo test",
        )

        assert result.success is True
        assert result.stdout == "output"
        assert result.return_code == 0

    def test_command_result_failure(self) -> None:
        """Failed command result."""
        result = CommandResult(
            stdout="",
            stderr="error message",
            return_code=1,
            command="invalid_cmd",
        )

        assert result.success is False
        assert result.stderr == "error message"
        assert result.return_code == 1

    def test_command_result_output(self) -> None:
        """Combined output property."""
        result = CommandResult(
            stdout="some output",
            stderr="some warning",
            return_code=0,
            command="test",
        )

        output = result.output
        assert "some output" in output
        assert "some warning" in output


class TestPermissionResult:
    """Tests for PermissionResult dataclass."""

    def test_allowed_permission(self) -> None:
        """Allowed permission result."""
        result = PermissionResult.allow()

        assert result.allowed is True
        assert result.level == PermissionLevel.ALLOWED
        assert result.requires_confirmation is False

    def test_blocked_permission(self) -> None:
        """Blocked permission result."""
        result = PermissionResult.block("Dangerous command")

        assert result.allowed is False
        assert result.level == PermissionLevel.BLOCKED
        assert result.message == "Dangerous command"

    def test_requires_confirmation_permission(self) -> None:
        """Permission requiring confirmation."""
        result = PermissionResult.confirm("Are you sure?")

        assert result.allowed is False
        assert result.level == PermissionLevel.REQUIRES_CONFIRMATION
        assert result.requires_confirmation is True
        assert result.message == "Are you sure?"


class TestSandboxIntegration:
    """Integration tests for sandbox with real commands."""

    @pytest.fixture
    def sandbox(self) -> Sandbox:
        """Create a fully configured sandbox."""
        soul = Soul(
            name="Harness",
            require_confirmation_patterns=["rm -rf*", "docker stop*"],
            auto_approve_patterns=["ls*", "cat*", "pwd*", "echo*", "date*"],
        )
        return Sandbox(soul)

    @pytest.mark.asyncio
    async def test_safe_command_workflow(self, sandbox: Sandbox) -> None:
        """Full workflow for a safe command."""
        command = "echo 'test message'"

        # Check permission
        perm = sandbox.check_command(command)
        assert perm.allowed is True

        # Execute
        result = await sandbox.execute(command)
        assert result.success is True
        assert "test message" in result.stdout

    @pytest.mark.asyncio
    async def test_confirmation_workflow(self, sandbox: Sandbox) -> None:
        """Workflow for a command requiring confirmation."""
        command = "rm -rf /tmp/test_dir"

        # Check permission
        perm = sandbox.check_command(command)
        assert perm.requires_confirmation is True
        assert perm.message is not None

        # In real scenario, would ask user and only execute if confirmed

    @pytest.mark.asyncio
    async def test_blocked_workflow(self, sandbox: Sandbox) -> None:
        """Workflow for a blocked command."""
        command = "rm -rf /"

        # Check permission
        perm = sandbox.check_command(command)
        assert perm.level == PermissionLevel.BLOCKED
        assert perm.allowed is False

        # Should never execute - the sandbox.execute() would need explicit override
