"""macOS LaunchAgent service management."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from rich.console import Console

from harness.config import get_harness_home

from .service import SERVICE_LABEL, _get_env_file_path

console = Console()


def _launchagent_dir() -> Path:
    """Get ~/Library/LaunchAgents/ directory."""
    return Path.home() / "Library" / "LaunchAgents"


def _launchagent_plist_path() -> Path:
    """Get path to the plist file."""
    return _launchagent_dir() / f"{SERVICE_LABEL}.plist"


def _generate_plist(ah_executable: str) -> str:
    """Generate launchd plist XML content."""
    harness_home = get_harness_home()
    log_dir = harness_home / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    stdout_log = log_dir / "harness-stdout.log"
    stderr_log = log_dir / "harness-stderr.log"

    # Determine ProgramArguments
    if " -m " in ah_executable:
        parts = ah_executable.split()
        program_args = "".join(f"        <string>{part}</string>\n" for part in parts)
    else:
        program_args = f"        <string>{ah_executable}</string>\n"

    program_args += "        <string>start</string>\n"
    program_args += "        <string>--foreground</string>\n"

    # Build EnvironmentVariables from .env file
    env_vars_section = ""
    env_file = _get_env_file_path()
    if env_file.exists():
        env_vars_section = "    <key>EnvironmentVariables</key>\n    <dict>\n"
        for raw_line in env_file.read_text().splitlines():
            entry = raw_line.strip()
            if entry and not entry.startswith("#") and "=" in entry:
                key, _, value = entry.partition("=")
                key = key.strip()
                value = value.strip().strip("\"'")
                env_vars_section += f"        <key>{key}</key>\n"
                env_vars_section += f"        <string>{value}</string>\n"
        env_vars_section += "    </dict>\n"

    plist = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{SERVICE_LABEL}</string>
            <key>ProgramArguments</key>
            <array>
        {program_args.rstrip()}
            </array>
            <key>WorkingDirectory</key>
            <string>{harness_home}</string>
            <key>KeepAlive</key>
            <true/>
            <key>RunAtLoad</key>
            <true/>
            <key>ThrottleInterval</key>
            <integer>10</integer>
            <key>StandardOutPath</key>
            <string>{stdout_log}</string>
            <key>StandardErrorPath</key>
            <string>{stderr_log}</string>
            {env_vars_section.rstrip()}
        </dict>
        </plist>
    """)

    return plist


def install_macos(ah_executable: str) -> None:
    """Install LaunchAgent on macOS."""
    import typer

    plist_path = _launchagent_plist_path()
    plist_dir = _launchagent_dir()

    plist_dir.mkdir(parents=True, exist_ok=True)

    # Unload if already loaded
    if plist_path.exists():
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            capture_output=True,
            check=False,
        )

    # Write plist
    plist_content = _generate_plist(ah_executable)
    plist_path.write_text(plist_content)

    console.print(f"[dim]Wrote plist: {plist_path}[/dim]")

    # Load the service
    result = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        console.print(f"[red]Failed to load service: {result.stderr}[/red]")
        raise typer.Exit(1)

    console.print("[green]Service installed and started.[/green]")
    console.print(f"[dim]Label: {SERVICE_LABEL}[/dim]")
    console.print()
    console.print("Useful commands:")
    console.print("  [cyan]launchctl list | grep agent-harness[/cyan]  — check status")
    console.print("  [cyan]ah uninstall-service[/cyan]                 — remove service")
    harness_home = get_harness_home()
    console.print(f"  [cyan]tail -f {harness_home}/logs/harness-stdout.log[/cyan]  — view logs")


def uninstall_macos() -> None:
    """Uninstall LaunchAgent on macOS."""
    plist_path = _launchagent_plist_path()

    if not plist_path.exists():
        console.print("[yellow]Service is not installed.[/yellow]")
        return

    result = subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    plist_path.unlink()

    if result.returncode == 0:
        console.print("[green]Service uninstalled successfully.[/green]")
    else:
        console.print("[yellow]Service file removed (was not loaded).[/yellow]")
