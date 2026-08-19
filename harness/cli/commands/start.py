"""
ah start/stop/status — Bot lifecycle management.

Start mode is determined by ENV in config.json:
- dev: foreground mode (logs to stdout)
- prod: daemon mode (background with PID file)
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console

from harness.config import ConfigManager
from harness.core.exceptions import BOUNDARY_ERRORS

console = Console()


def _get_pid_file() -> Path:
    """Get path to PID file."""
    manager = ConfigManager()
    return manager.pid_file


def _read_pid() -> int | None:
    """Read PID from file, return None if not found or invalid."""
    pid_file = _get_pid_file()

    if not pid_file.exists():
        return None

    try:
        pid = int(pid_file.read_text().strip())
        return pid
    except (OSError, ValueError):
        return None


def _write_pid(pid: int) -> None:
    """Write PID to file."""
    pid_file = _get_pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(pid))


def _remove_pid() -> None:
    """Remove PID file."""
    pid_file = _get_pid_file()
    if pid_file.exists():
        pid_file.unlink()


def _is_process_running(pid: int) -> bool:
    """Check if a process with given PID is running."""
    try:
        os.kill(pid, 0)  # Signal 0 just checks if process exists
        return True
    except OSError:
        return False


def _get_running_status() -> tuple[bool, int | None]:
    """
    Get running status.

    :returns: Tuple of (is_running, pid or None).
    """
    pid = _read_pid()

    if pid is None:
        return False, None

    if _is_process_running(pid):
        return True, pid
    else:
        # Stale PID file
        _remove_pid()
        return False, None


def _run_foreground() -> None:
    """Run the bot in foreground mode."""
    console.print("[cyan]Starting Agent Harness in foreground mode...[/cyan]")
    console.print("[dim]Press Ctrl+C to stop.[/dim]")
    console.print()

    # Import and run main
    try:
        from harness.runtime import run_harness

        asyncio.run(run_harness())
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")
    except ImportError:
        # Fallback to running main.py directly
        console.print("[dim]Running main.py directly...[/dim]")
        main_py = Path(__file__).parent.parent.parent.parent / "main.py"
        if main_py.exists():
            os.execv(sys.executable, [sys.executable, str(main_py)])
        else:
            console.print("[red]Cannot find main.py or harness.runtime[/red]")
            raise typer.Exit(1)


def _run_daemon() -> int:
    """
    Run the bot in daemon mode.

    :returns: PID of the daemon process.
    """
    console.print("[cyan]Starting Agent Harness in daemon mode...[/cyan]")

    # Find main.py
    main_py = Path(__file__).parent.parent.parent.parent / "main.py"
    if not main_py.exists():
        console.print("[red]Cannot find main.py[/red]")
        raise typer.Exit(1)

    # Get log file path
    manager = ConfigManager()
    log_file = manager.logs_dir / "harness.log"

    # Start the process in background
    with open(log_file, "a") as stdout_file:
        process = subprocess.Popen(
            [sys.executable, str(main_py)],
            stdout=stdout_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # Detach from terminal
            cwd=str(main_py.parent),
        )

    # Write PID file
    _write_pid(process.pid)

    # Wait a moment and check if it started successfully
    time.sleep(1)

    if _is_process_running(process.pid):
        console.print(f"[green]Started successfully (PID: {process.pid})[/green]")
        console.print(f"[dim]Logs: {log_file}[/dim]")
        return process.pid
    else:
        console.print("[red]Process failed to start. Check logs:[/red]")
        console.print(f"  {log_file}")
        _remove_pid()
        raise typer.Exit(1)


def start_command(
    foreground: bool = typer.Option(
        False,
        "--foreground",
        "-f",
        help="Force foreground mode (ignore ENV setting).",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        "-d",
        help="Force daemon mode (ignore ENV setting).",
    ),
) -> None:
    """
    Start the Agent Harness bot.

    By default, mode is determined by ENV in config.json:
    - dev: foreground mode (logs to stdout)
    - prod: daemon mode (background with PID file)

    Use --foreground or --daemon to override.
    """
    # Check if already running
    running, pid = _get_running_status()
    if running:
        console.print(f"[yellow]Agent Harness is already running (PID: {pid})[/yellow]")
        console.print("Use [cyan]ah stop[/cyan] to stop it first.")
        raise typer.Exit(1)

    # Determine mode
    if foreground and daemon:
        console.print("[red]Cannot specify both --foreground and --daemon[/red]")
        raise typer.Exit(1)

    if foreground:
        use_daemon = False
    elif daemon:
        use_daemon = True
    else:
        # Determine from config
        manager = ConfigManager()
        config = manager.load()
        use_daemon = config.env == "prod"

    # Run
    if use_daemon:
        _run_daemon()
    else:
        _run_foreground()


def stop_command(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force stop (SIGKILL instead of SIGTERM).",
    ),
    timeout: int = typer.Option(
        10,
        "--timeout",
        "-t",
        help="Seconds to wait for graceful shutdown before forcing.",
    ),
) -> None:
    """
    Stop the running Agent Harness bot.
    """
    running, pid = _get_running_status()

    if not running:
        console.print("[yellow]Agent Harness is not running.[/yellow]")
        return

    console.print(f"[cyan]Stopping Agent Harness (PID: {pid})...[/cyan]")

    try:
        if force:
            os.kill(pid, signal.SIGKILL)
            console.print("[yellow]Sent SIGKILL[/yellow]")
        else:
            os.kill(pid, signal.SIGTERM)
            console.print("[dim]Sent SIGTERM, waiting for graceful shutdown...[/dim]")

            # Wait for process to exit
            for _ in range(timeout):
                if not _is_process_running(pid):
                    break
                time.sleep(1)
            else:
                # Timeout reached, force kill
                console.print(
                    f"[yellow]Timeout after {timeout}s, sending SIGKILL...[/yellow]"
                )
                os.kill(pid, signal.SIGKILL)

        # Clean up PID file
        _remove_pid()
        console.print("[green]Stopped successfully.[/green]")

    except OSError as e:
        console.print(f"[red]Failed to stop: {e}[/red]")
        _remove_pid()
        raise typer.Exit(1)


def status_command() -> None:
    """
    Show the status of the Agent Harness bot.
    """
    running, pid = _get_running_status()

    if running:
        console.print(f"[green]Agent Harness is running[/green] (PID: {pid})")

        # Show uptime/memory when psutil is available
        try:
            import psutil
        except ImportError:
            psutil = None  # type: ignore[assignment]

        if psutil is not None:
            try:
                proc = psutil.Process(pid)
                create_time = proc.create_time()
                uptime_seconds = time.time() - create_time

                hours, remainder = divmod(int(uptime_seconds), 3600)
                minutes, seconds = divmod(remainder, 60)

                console.print(f"[dim]Uptime: {hours}h {minutes}m {seconds}s[/dim]")
                console.print(
                    f"[dim]Memory: {proc.memory_info().rss / 1024 / 1024:.1f} MB[/dim]"
                )
            except BOUNDARY_ERRORS as exc:
                console.print(f"[dim]Could not read process stats: {exc}[/dim]")

        # Show log file location
        manager = ConfigManager()
        log_file = manager.logs_dir / "harness.log"
        if log_file.exists():
            console.print(f"[dim]Logs: {log_file}[/dim]")
    else:
        console.print("[yellow]Agent Harness is not running.[/yellow]")
        console.print("Start with: [cyan]ah start[/cyan]")
