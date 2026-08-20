"""Linux systemd user service management."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import typer
from rich.console import Console

from harness.config import get_harness_home

from .service import SYSTEMD_UNIT_NAME, _get_env_file_path

console = Console()


def _systemd_user_dir() -> Path:
    """Get ~/.config/systemd/user/ directory."""
    return Path.home() / ".config" / "systemd" / "user"


def _systemd_unit_path() -> Path:
    """Get path to the systemd unit file."""
    return _systemd_user_dir() / SYSTEMD_UNIT_NAME


def _generate_systemd_unit(ah_executable: str) -> str:
    """Generate systemd user unit file content."""
    harness_home = get_harness_home()
    log_dir = harness_home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Build Environment lines from .env file
    env_lines = ""
    env_file = _get_env_file_path()
    if env_file.exists():
        for raw_line in env_file.read_text().splitlines():
            entry = raw_line.strip()
            if entry and not entry.startswith("#") and "=" in entry:
                key, _, value = entry.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                env_lines += f'Environment="{key}={value}"\n'

    exec_start = f"{ah_executable} start --foreground"

    unit = textwrap.dedent(f"""\
        [Unit]
        Description=Agent Harness — Multi-agent AI bot
        After=network-online.target postgresql.service
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart={exec_start}
        WorkingDirectory={harness_home}
        Restart=on-failure
        RestartSec=10
        StandardOutput=append:{log_dir}/harness-stdout.log
        StandardError=append:{log_dir}/harness-stderr.log

        # Environment
        {env_lines.rstrip()}

        # Security hardening
        NoNewPrivileges=true
        ProtectSystem=strict
        ProtectHome=read-only
        ReadWritePaths={harness_home}

        [Install]
        WantedBy=default.target
    """)

    return unit


def install_linux(ah_executable: str) -> None:
    """Install systemd user service on Linux."""
    unit_path = _systemd_unit_path()
    unit_dir = _systemd_user_dir()

    unit_dir.mkdir(parents=True, exist_ok=True)

    # Stop existing service if running
    subprocess.run(
        ["systemctl", "--user", "stop", SYSTEMD_UNIT_NAME],
        capture_output=True,
        check=False,
    )

    # Write unit file
    unit_content = _generate_systemd_unit(ah_executable)
    unit_path.write_text(unit_content)

    console.print(f"[dim]Wrote unit: {unit_path}[/dim]")

    # Reload systemd daemon
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True,
        check=False,
    )

    # Enable the service (start on login)
    result = subprocess.run(
        ["systemctl", "--user", "enable", SYSTEMD_UNIT_NAME],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        console.print(f"[red]Failed to enable service: {result.stderr}[/red]")
        raise typer.Exit(1)

    # Start the service now
    result = subprocess.run(
        ["systemctl", "--user", "start", SYSTEMD_UNIT_NAME],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        console.print(
            f"[yellow]Warning: service enabled but failed to start: {result.stderr}[/yellow]"
        )
        console.print("[dim]It will start automatically on next login.[/dim]")
    else:
        console.print("[green]Service installed and started.[/green]")

    console.print(f"[dim]Unit: {SYSTEMD_UNIT_NAME}[/dim]")
    console.print()
    console.print("Useful commands:")
    console.print(f"  [cyan]systemctl --user status {SYSTEMD_UNIT_NAME}[/cyan]  — check status")
    console.print(f"  [cyan]journalctl --user -u {SYSTEMD_UNIT_NAME} -f[/cyan]   — view logs")
    console.print("  [cyan]ah uninstall-service[/cyan]                           — remove service")

    # Enable lingering so the service runs even when user is not logged in
    username = os.environ.get("USER", "")
    if username:
        linger_result = subprocess.run(
            ["loginctl", "enable-linger", username],
            capture_output=True,
            text=True,
            check=False,
        )
        if linger_result.returncode == 0:
            console.print(
                f"[dim]Lingering enabled for user '{username}' "
                "— service runs without active login.[/dim]"
            )
        else:
            console.print(
                "[yellow]Could not enable lingering (may need sudo): "
                f"sudo loginctl enable-linger {username}[/yellow]"
            )


def uninstall_linux() -> None:
    """Uninstall systemd user service on Linux."""
    unit_path = _systemd_unit_path()

    if not unit_path.exists():
        console.print("[yellow]Service is not installed.[/yellow]")
        return

    # Stop and disable
    subprocess.run(
        ["systemctl", "--user", "stop", SYSTEMD_UNIT_NAME],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["systemctl", "--user", "disable", SYSTEMD_UNIT_NAME],
        capture_output=True,
        check=False,
    )

    # Remove unit file
    unit_path.unlink()

    # Reload daemon
    subprocess.run(
        ["systemctl", "--user", "daemon-reload"],
        capture_output=True,
        check=False,
    )

    console.print("[green]Service uninstalled successfully.[/green]")
