"""
ah init — Setup wizard for Agent Harness.

Supports both interactive wizard mode and one-shot mode with flags.
Migrates existing ./config/*.yaml to ~/.agent-harness/.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel

from harness.cli.commands.init_helpers import (
    OneshotOptions,
    convert_mcp_yaml_to_json,
    convert_soul_yaml_to_md,
    find_existing_configs,
    load_env_file,
    print_setup_complete,
    prompt_database,
    prompt_environment,
    prompt_ollama,
    prompt_telegram,
    save_wizard_artifacts,
)
from harness.config import ConfigManager, HarnessConfig
from harness.config.defaults import DEFAULT_MCP, DEFAULT_SOUL_MD

console = Console()


def _run_wizard(
    force: bool,
    existing_configs: dict,
) -> HarnessConfig | None:
    """Run interactive setup wizard."""
    console.print()
    console.print(
        Panel(
            "[bold cyan]Agent Harness — Setup Wizard[/bold cyan]\n\n"
            "This wizard will help you configure Agent Harness.\n"
            "Press [bold]Ctrl+C[/bold] to cancel at any time.",
            expand=False,
        )
    )
    console.print()

    manager = ConfigManager()
    if manager.exists() and not force:
        console.print("[yellow]Configuration already exists at:[/yellow]")
        console.print(f"  {manager.config_dir}")
        console.print()
        if not typer.confirm("Overwrite existing configuration?", default=False):
            console.print("[dim]Setup cancelled.[/dim]")
            return None

    migrate = False
    if existing_configs:
        console.print("[cyan]Found existing configuration files:[/cyan]")
        for name, path in existing_configs.items():
            console.print(f"  • {name}: {path}")
        console.print()
        migrate = typer.confirm("Migrate these to ~/.agent-harness/?", default=True)

    env_values = load_env_file(existing_configs["env"]) if "env" in existing_configs else {}
    telegram_token, user_ids = prompt_telegram(env_values)
    db_url = prompt_database(env_values)
    ollama_url, ollama_model = prompt_ollama(env_values)
    env = prompt_environment()

    config = HarnessConfig(
        env=env,
        telegram={"token": telegram_token, "allowed_user_ids": user_ids},
        database={"url": db_url},
        llm={"model": ollama_model, "api_base": ollama_url},
    )
    save_wizard_artifacts(manager, config, migrate=migrate, existing_configs=existing_configs)
    print_setup_complete(manager.config_dir)
    return config


def _run_oneshot(opts: OneshotOptions) -> HarnessConfig | None:
    """Run one-shot configuration."""
    manager = ConfigManager()

    if manager.exists() and not opts.force:
        console.print("[red]Configuration already exists. Use --force to overwrite.[/red]")
        raise typer.Exit(1)

    allowed_ids: list[int] = []
    if opts.user_ids:
        allowed_ids = [int(x.strip()) for x in opts.user_ids.split(",") if x.strip().isdigit()]

    config = HarnessConfig(
        env=opts.env,
        telegram={"token": opts.telegram_token, "allowed_user_ids": allowed_ids},
        database={"url": opts.db_url},
        llm={"model": opts.ollama_model, "api_base": opts.ollama_url},
    )
    manager.save(config)

    existing_configs = find_existing_configs()
    if "soul" in existing_configs:
        soul_content = convert_soul_yaml_to_md(existing_configs["soul"])
    else:
        soul_content = DEFAULT_SOUL_MD
    (manager.config_dir / "soul.md").write_text(soul_content)

    if "mcp" in existing_configs:
        mcp_data = convert_mcp_yaml_to_json(existing_configs["mcp"])
    else:
        mcp_data = DEFAULT_MCP
    (manager.config_dir / "mcp.json").write_text(json.dumps(mcp_data, indent=2))

    console.print(f"[green]Configuration saved to {manager.config_dir}[/green]")
    return config


def init_command(
    telegram_token: str | None = typer.Option(
        None,
        "--telegram-token",
        "-t",
        help="Telegram bot token from @BotFather.",
    ),
    db_url: str | None = typer.Option(
        None,
        "--db-url",
        "-d",
        help="PostgreSQL connection URL.",
    ),
    ollama_url: str = typer.Option(
        "http://localhost:11434",
        "--ollama-url",
        "-o",
        help="Ollama server URL.",
    ),
    ollama_model: str = typer.Option(
        "ollama_chat/llama3.1",
        "--ollama-model",
        "-m",
        help="Ollama model to use.",
    ),
    env: str = typer.Option(
        "dev",
        "--env",
        "-e",
        help="Environment: dev or prod.",
    ),
    user_ids: str | None = typer.Option(
        None,
        "--user-ids",
        "-u",
        help="Allowed Telegram user IDs (comma-separated).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing configuration.",
    ),
) -> None:
    """
    Setup wizard — configure Agent Harness.

    Run without flags for interactive mode, or provide all required flags for one-shot mode.

    Examples:

        ah init                                    # Interactive wizard

        ah init --telegram-token=123:ABC \\
                --db-url=postgresql://... \\
                --user-ids=123456                  # One-shot
    """
    try:
        if telegram_token and db_url:
            _run_oneshot(
                OneshotOptions(
                    telegram_token=telegram_token,
                    db_url=db_url,
                    ollama_url=ollama_url,
                    ollama_model=ollama_model,
                    env=env,
                    user_ids=user_ids,
                    force=force,
                )
            )
        else:
            _run_wizard(force=force, existing_configs=find_existing_configs())
    except KeyboardInterrupt:
        console.print("\n[yellow]Setup cancelled.[/yellow]")
        raise typer.Exit(0)
