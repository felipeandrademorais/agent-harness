"""
ah install-service / uninstall-service — System service management.

Installs Agent Harness as a background service:
- macOS: LaunchAgent plist in ~/Library/LaunchAgents/
- Linux: systemd user unit in ~/.config/systemd/user/

The service auto-starts on login and restarts on failure.
"""

from __future__ import annotations

import platform
import shutil
import sys

import typer
from rich.console import Console

from harness.config import ConfigManager, get_harness_home

console = Console()

SERVICE_LABEL = "com.agent-harness.bot"
SYSTEMD_UNIT_NAME = "agent-harness.service"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_platform() -> str:
    """Detect current platform: 'macos' or 'linux'."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "linux":
        return "linux"
    else:
        console.print(f"[red]Unsupported platform: {system}[/red]")
        console.print("Only macOS and Linux are supported.")
        raise typer.Exit(1)


def _find_ah_executable() -> str:
    """Find the absolute path to the `ah` executable."""
    ah_path = shutil.which("ah")
    if ah_path:
        return ah_path

    from pathlib import Path

    scripts_dir = Path(sys.executable).parent
    ah_candidate = scripts_dir / "ah"
    if ah_candidate.exists():
        return str(ah_candidate)

    return f"{sys.executable} -m harness.cli"


def _get_env_file_path():
    """Get path to the .env file for the service."""
    return get_harness_home() / ".env"


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------


def install_service_command() -> None:
    """
    Install Agent Harness as a system service.

    On macOS: creates a LaunchAgent (runs on user login, auto-restarts).
    On Linux: creates a systemd user service (runs on login, auto-restarts).

    The service runs `ah start --foreground` under process supervision.
    Environment variables are loaded from ~/.agent-harness/.env.
    """
    plat = _detect_platform()
    ah_executable = _find_ah_executable()

    console.print("[cyan]Installing Agent Harness as a service...[/cyan]")
    console.print(f"[dim]Platform: {plat}[/dim]")
    console.print(f"[dim]Executable: {ah_executable}[/dim]")
    console.print()

    # Check that config exists
    manager = ConfigManager()
    if not manager.exists():
        console.print("[red]Configuration not found. Run 'ah init' first.[/red]")
        raise typer.Exit(1)

    # Ensure .env file exists for the service
    env_file = _get_env_file_path()
    if not env_file.exists():
        console.print(f"[yellow]Warning: No .env file found at {env_file}[/yellow]")
        console.print(
            "[yellow]The service needs environment variables "
            "(TELEGRAM_TOKEN, DATABASE_URL).[/yellow]"
        )
        console.print(f"[dim]Create it with: cp .env.example {env_file}[/dim]")
        console.print()

        if not typer.confirm("Continue anyway?", default=False):
            raise typer.Exit(0)

    if plat == "macos":
        from harness.cli.commands._service_macos import install_macos

        install_macos(ah_executable)
    else:
        from harness.cli.commands._service_linux import install_linux

        install_linux(ah_executable)


def uninstall_service_command() -> None:
    """
    Uninstall the Agent Harness system service.

    Stops and removes the service configuration file.
    """
    plat = _detect_platform()

    console.print("[cyan]Uninstalling Agent Harness service...[/cyan]")

    if plat == "macos":
        from harness.cli.commands._service_macos import uninstall_macos

        uninstall_macos()
    else:
        from harness.cli.commands._service_linux import uninstall_linux

        uninstall_linux()
