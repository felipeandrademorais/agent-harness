"""
Agent Harness CLI — main entry point.

Usage::

    ah --help           # Show help
    ah --version        # Show version
    ah init             # Setup wizard
    ah start            # Start the bot
    ah stop             # Stop the bot
    ah status           # Show bot status
    ah doctor           # Health check
    ah config show      # Show config
    ah skills list      # List skills
"""
from __future__ import annotations

import typer
from rich.console import Console

from harness.cli.commands import config_cmd, doctor, init, skills, start

__version__ = "0.1.0"

# Main Typer app
app = typer.Typer(
    name="ah",
    help="Agent Harness — Multi-agent AI system with Telegram, Ollama, MCP and PostgreSQL.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Console for rich output
console = Console()


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f"[bold cyan]Agent Harness[/bold cyan] v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """
    Agent Harness — Multi-agent AI system.
    
    Run [bold cyan]ah init[/bold cyan] to get started.
    """
    pass


# Register subcommands
app.command(name="init", help="Setup wizard — configure Agent Harness.")(init.init_command)
app.command(name="start", help="Start the Agent Harness bot.")(start.start_command)
app.command(name="stop", help="Stop the running bot.")(start.stop_command)
app.command(name="status", help="Show bot status.")(start.status_command)
app.command(name="doctor", help="Health check — verify all components.")(doctor.doctor_command)

# Register subcommand groups
app.add_typer(config_cmd.app, name="config", help="Manage configuration.")
app.add_typer(skills.app, name="skills", help="Manage skills.")


if __name__ == "__main__":
    app()
