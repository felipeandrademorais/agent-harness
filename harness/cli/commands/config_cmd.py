"""
ah config — Configuration management commands.

Commands:
- show: Display current configuration
- set: Set a configuration value
- edit: Open configuration in editor
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import typer
from rich.console import Console
from rich.tree import Tree

from harness.config import ConfigManager

console = Console()

app = typer.Typer(
    name="config",
    help="Manage Agent Harness configuration.",
    no_args_is_help=True,
)


def _redact_value(key: str, value: Any) -> Any:
    """Redact sensitive values."""
    # Only redact specific known secret keys
    secret_keys = {"token", "api_key", "password", "secret"}
    # Check if the final key part matches a secret key
    final_key = key.split(".")[-1].lower()

    if final_key in secret_keys and value:
        if isinstance(value, str) and len(value) > 8:
            return value[:4] + "***" + value[-4:]
        return "***"

    # Redact database URLs (contain passwords)
    if final_key == "url" and value and isinstance(value, str) and "@" in value:
        # postgresql://user:pass@host:port/db -> postgresql://***@host:port/db
        if "://" in value:
            prefix, rest = value.split("://", 1)
            if "@" in rest:
                creds, host = rest.split("@", 1)
                return f"{prefix}://***@{host}"

    return value


def _build_tree(data: dict, tree: Tree, redact: bool = True, prefix: str = "") -> None:
    """Recursively build a Rich tree from config dict."""
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict):
            branch = tree.add(f"[cyan]{key}[/cyan]")
            _build_tree(value, branch, redact, full_key)
        elif isinstance(value, list):
            if value:
                display_value = _redact_value(full_key, value) if redact else value
                tree.add(f"[cyan]{key}[/cyan]: {display_value}")
            else:
                tree.add(f"[cyan]{key}[/cyan]: [dim][]")
        else:
            display_value = _redact_value(full_key, value) if redact else value
            if display_value is None:
                tree.add(f"[cyan]{key}[/cyan]: [dim]null[/dim]")
            else:
                tree.add(f"[cyan]{key}[/cyan]: {display_value}")


@app.command(name="show")
def show_command(
    raw: bool = typer.Option(
        False,
        "--raw",
        "-r",
        help="Output raw JSON without formatting or redaction.",
    ),
    section: str | None = typer.Option(
        None,
        "--section",
        "-s",
        help="Show only a specific section (e.g., telegram, database, llm).",
    ),
    no_redact: bool = typer.Option(
        False,
        "--no-redact",
        help="Show secrets without redaction.",
    ),
) -> None:
    """
    Show current configuration.

    Secrets are redacted unless --no-redact is specified.

    Examples:
        ah config show                    # Full config, formatted
        ah config show --raw              # Raw JSON
        ah config show -s telegram        # Only telegram section
    """
    manager = ConfigManager()

    if not manager.exists():
        console.print("[yellow]No configuration found.[/yellow]")
        console.print("Run [cyan]ah init[/cyan] to create configuration.")
        raise typer.Exit(1)

    # Load config
    config = manager.load()
    data = config.model_dump(mode="json")

    # Filter by section if specified
    if section:
        if section not in data:
            console.print(f"[red]Unknown section: {section}[/red]")
            console.print(f"Available sections: {', '.join(data.keys())}")
            raise typer.Exit(1)
        data = {section: data[section]}

    # Raw JSON output
    if raw:
        console.print(json.dumps(data, indent=2))
        return

    # Formatted tree output
    console.print()
    console.print(
        f"[bold cyan]Configuration[/bold cyan] [dim]({manager.config_file})[/dim]"
    )
    console.print()

    tree = Tree("[bold]config.json[/bold]")
    _build_tree(data, tree, redact=not no_redact)
    console.print(tree)
    console.print()


@app.command(name="set")
def set_command(
    key: str = typer.Argument(
        ...,
        help="Configuration key in dot notation (e.g., telegram.token, llm.model).",
    ),
    value: str = typer.Argument(
        ...,
        help="Value to set.",
    ),
) -> None:
    """
    Set a configuration value.

    Use dot notation for nested keys:

        ah config set env prod
        ah config set telegram.token "123:ABC"
        ah config set llm.model "ollama_chat/llama3.2"
        ah config set llm.temperature 0.8
        ah config set telegram.allowed_user_ids "123,456,789"

    For list values (like allowed_user_ids), provide comma-separated values.
    """
    manager = ConfigManager()

    if not manager.exists():
        console.print("[yellow]No configuration found.[/yellow]")
        console.print("Run [cyan]ah init[/cyan] first.")
        raise typer.Exit(1)

    # Load config
    config = manager.load()

    # Parse the key path
    parts = key.split(".")

    # Navigate to parent object
    obj = config
    for part in parts[:-1]:
        if hasattr(obj, part):
            obj = getattr(obj, part)
        else:
            console.print(f"[red]Invalid config key: {key}[/red]")
            raise typer.Exit(1)

    final_key = parts[-1]

    if not hasattr(obj, final_key):
        console.print(f"[red]Invalid config key: {key}[/red]")
        raise typer.Exit(1)

    # Get current value to determine type
    current_value = getattr(obj, final_key)

    # Convert value to appropriate type
    try:
        if isinstance(current_value, bool):
            new_value = value.lower() in ("true", "1", "yes", "on")
        elif isinstance(current_value, int):
            new_value = int(value)
        elif isinstance(current_value, float):
            new_value = float(value)
        elif isinstance(current_value, list):
            # Parse comma-separated list
            if current_value and isinstance(current_value[0], int):
                new_value = [int(x.strip()) for x in value.split(",") if x.strip()]
            else:
                new_value = [x.strip() for x in value.split(",") if x.strip()]
        else:
            new_value = value
    except ValueError as e:
        console.print(f"[red]Invalid value for {key}: {e}[/red]")
        raise typer.Exit(1)

    # Set the value
    setattr(obj, final_key, new_value)

    # Save
    manager.save(config)

    # Display result
    display_value = _redact_value(key, new_value)
    console.print(f"[green]Set[/green] {key} = {display_value}")


@app.command(name="edit")
def edit_command(
    soul: bool = typer.Option(
        False,
        "--soul",
        help="Edit soul.md instead of config.json.",
    ),
    mcp: bool = typer.Option(
        False,
        "--mcp",
        help="Edit mcp.json instead of config.json.",
    ),
) -> None:
    """
    Open configuration file in $EDITOR.

    By default, opens config.json. Use --soul or --mcp to edit other files.

    Examples:
        ah config edit          # Edit config.json
        ah config edit --soul   # Edit soul.md
        ah config edit --mcp    # Edit mcp.json
    """
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "vim"))
    manager = ConfigManager()

    if soul:
        target = manager.soul_file
        if not target.exists():
            console.print(f"[yellow]Soul file not found: {target}[/yellow]")
            console.print("Run [cyan]ah init[/cyan] first.")
            raise typer.Exit(1)
    elif mcp:
        target = manager.mcp_file
        if not target.exists():
            console.print(f"[yellow]MCP file not found: {target}[/yellow]")
            console.print("Run [cyan]ah init[/cyan] first.")
            raise typer.Exit(1)
    else:
        target = manager.config_file
        if not target.exists():
            console.print(f"[yellow]Config file not found: {target}[/yellow]")
            console.print("Run [cyan]ah init[/cyan] first.")
            raise typer.Exit(1)

    console.print(f"[dim]Opening {target} in {editor}...[/dim]")

    try:
        subprocess.run([editor, str(target)], check=True)
        console.print(f"[green]Saved {target.name}[/green]")
    except subprocess.CalledProcessError:
        console.print("[red]Editor exited with error.[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print(f"[red]Editor not found: {editor}[/red]")
        console.print("Set the EDITOR environment variable to your preferred editor.")
        raise typer.Exit(1)


@app.command(name="path")
def path_command() -> None:
    """
    Show the path to the configuration directory.
    """
    manager = ConfigManager()
    console.print(str(manager.config_dir))
