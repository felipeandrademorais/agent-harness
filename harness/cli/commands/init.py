"""
ah init — Setup wizard for Agent Harness.

Supports both interactive wizard mode and one-shot mode with flags.
Migrates existing ./config/*.yaml to ~/.agent-harness/.
"""
from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from harness.config import ConfigManager, HarnessConfig, get_harness_home
from harness.config.defaults import DEFAULT_CONFIG, DEFAULT_MCP, DEFAULT_SOUL_MD

console = Console()


def _list_ollama_models(base_url: str) -> list[str]:
    """Fetch available Ollama models."""
    try:
        url = base_url.rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def _find_existing_configs() -> dict[str, Path]:
    """Find existing config files in ./config/ directory."""
    found = {}
    config_dir = Path.cwd() / "config"
    
    if config_dir.exists():
        if (config_dir / "soul.yaml").exists():
            found["soul"] = config_dir / "soul.yaml"
        if (config_dir / "mcp.yaml").exists():
            found["mcp"] = config_dir / "mcp.yaml"
        if (config_dir / "skills.yaml").exists():
            found["skills"] = config_dir / "skills.yaml"
    
    # Check for .env file
    env_file = Path.cwd() / ".env"
    if env_file.exists():
        found["env"] = env_file
    
    return found


def _load_env_file(path: Path) -> dict[str, str]:
    """Load values from a .env file."""
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def _convert_soul_yaml_to_md(yaml_path: Path) -> str:
    """Convert soul.yaml to soul.md format."""
    with yaml_path.open() as f:
        data = yaml.safe_load(f) or {}
    
    # Build frontmatter
    personality = data.get("personality", {})
    behaviors = data.get("behaviors", {})
    
    frontmatter = {
        "name": data.get("name", "Harness"),
        "version": data.get("version", "1.0"),
        "mood": personality.get("mood", "professional"),
        "language": personality.get("language", "pt-BR"),
        "values": personality.get("values", []),
        "behaviors": {
            "require_confirmation": behaviors.get("require_confirmation", []),
            "auto_approve": behaviors.get("auto_approve", []),
        },
    }
    
    # Get system prompt template or capabilities description
    body = data.get("system_prompt_template", "")
    if not body:
        capabilities = data.get("capabilities", {}).get("description", "")
        tone = personality.get("tone", "")
        body = f"""Você é {{name}}, um assistente de IA com comportamento agêntico.

## Personalidade

Mood: {{mood}}

{tone}

## Idioma

Responda sempre em {{language}}.

## Valores

{{values}}

## Capacidades

{capabilities}
"""
    
    # Combine frontmatter and body
    frontmatter_yaml = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
    return f"---\n{frontmatter_yaml}---\n\n{body}"


def _convert_mcp_yaml_to_json(yaml_path: Path) -> dict:
    """Convert mcp.yaml to mcp.json format."""
    with yaml_path.open() as f:
        data = yaml.safe_load(f) or {}
    
    servers = []
    for server in data.get("servers", []):
        servers.append({
            "name": server.get("name", "unknown"),
            "type": server.get("type", "stdio"),
            "command": server.get("command", []),
            "url": server.get("url"),
            "env": server.get("env", {}),
            "enabled": True,
        })
    
    return {"servers": servers}


def _run_wizard(
    force: bool,
    existing_configs: dict[str, Path],
) -> Optional[HarnessConfig]:
    """Run interactive setup wizard."""
    console.print()
    console.print(Panel(
        "[bold cyan]Agent Harness — Setup Wizard[/bold cyan]\n\n"
        "This wizard will help you configure Agent Harness.\n"
        "Press [bold]Ctrl+C[/bold] to cancel at any time.",
        expand=False,
    ))
    console.print()
    
    manager = ConfigManager()
    
    # Check if config already exists
    if manager.exists() and not force:
        console.print("[yellow]Configuration already exists at:[/yellow]")
        console.print(f"  {manager.config_dir}")
        console.print()
        if not typer.confirm("Overwrite existing configuration?", default=False):
            console.print("[dim]Setup cancelled.[/dim]")
            return None
    
    # Migrate existing configs?
    migrate = False
    if existing_configs:
        console.print("[cyan]Found existing configuration files:[/cyan]")
        for name, path in existing_configs.items():
            console.print(f"  • {name}: {path}")
        console.print()
        migrate = typer.confirm("Migrate these to ~/.agent-harness/?", default=True)
    
    # Load values from .env if available
    env_values = {}
    if "env" in existing_configs:
        env_values = _load_env_file(existing_configs["env"])
    
    # Step 1: Telegram
    console.print()
    console.rule("[bold]Step 1/4 — Telegram Bot[/bold]")
    console.print("  Get your bot token from @BotFather: https://t.me/BotFather")
    console.print()
    
    default_token = env_values.get("TELEGRAM_TOKEN", "")
    telegram_token = typer.prompt(
        "Telegram bot token",
        default=default_token if default_token else None,
        hide_input=True,
    )
    
    console.print()
    console.print("  Get your user ID from @userinfobot: https://t.me/userinfobot")
    console.print()
    
    default_ids = env_values.get("ALLOWED_USER_IDS", "")
    user_ids_str = typer.prompt(
        "Allowed user IDs (comma-separated)",
        default=default_ids if default_ids else None,
    )
    user_ids = [int(x.strip()) for x in user_ids_str.split(",") if x.strip().isdigit()]
    
    # Step 2: Database
    console.print()
    console.rule("[bold]Step 2/4 — PostgreSQL Database[/bold]")
    console.print("  The harness uses PostgreSQL for conversation history.")
    console.print()
    
    default_db = env_values.get("DATABASE_URL", "postgresql://harness:harness@localhost:5455/harness")
    use_docker = typer.confirm("Use Docker PostgreSQL on port 5455?", default=True)
    
    if use_docker:
        db_url = "postgresql://harness:harness@localhost:5455/harness"
        console.print("[dim]  Run: docker compose up -d db[/dim]")
    else:
        db_url = typer.prompt("Database URL", default=default_db)
    
    # Step 3: Ollama
    console.print()
    console.rule("[bold]Step 3/4 — Ollama LLM[/bold]")
    console.print("  Ollama provides local LLM inference.")
    console.print()
    
    default_ollama_url = env_values.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_url = typer.prompt("Ollama URL", default=default_ollama_url)
    
    # Try to list models
    models = _list_ollama_models(ollama_url)
    if models:
        console.print(f"[green]  Found {len(models)} model(s): {', '.join(models[:5])}{'...' if len(models) > 5 else ''}[/green]")
        default_model = env_values.get("OLLAMA_MODEL", f"ollama_chat/{models[0]}")
    else:
        console.print("[yellow]  Could not connect to Ollama. Make sure it's running.[/yellow]")
        default_model = env_values.get("OLLAMA_MODEL", "ollama_chat/llama3.1")
    
    ollama_model = typer.prompt("Model (LiteLLM format)", default=default_model)
    
    # Step 4: Environment
    console.print()
    console.rule("[bold]Step 4/4 — Environment[/bold]")
    console.print("  dev = foreground mode (logs to stdout)")
    console.print("  prod = daemon mode (background with heartbeat)")
    console.print()
    
    env = typer.prompt("Environment", default="dev")
    if env not in ("dev", "prod"):
        console.print("[yellow]Invalid environment, using 'dev'.[/yellow]")
        env = "dev"
    
    # Create config
    config = HarnessConfig(
        env=env,
        telegram={"token": telegram_token, "allowed_user_ids": user_ids},
        database={"url": db_url},
        llm={"model": ollama_model, "api_base": ollama_url},
    )
    
    # Save config
    console.print()
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Saving configuration...", total=None)
        
        manager.save(config)
        progress.update(task, description="Saved config.json")
        
        # Migrate or create soul.md
        if migrate and "soul" in existing_configs:
            soul_content = _convert_soul_yaml_to_md(existing_configs["soul"])
            progress.update(task, description="Migrated soul.yaml → soul.md")
        else:
            soul_content = DEFAULT_SOUL_MD
            progress.update(task, description="Created default soul.md")
        
        (manager.config_dir / "soul.md").write_text(soul_content)
        
        # Migrate or create mcp.json
        if migrate and "mcp" in existing_configs:
            mcp_data = _convert_mcp_yaml_to_json(existing_configs["mcp"])
            progress.update(task, description="Migrated mcp.yaml → mcp.json")
        else:
            mcp_data = DEFAULT_MCP
            progress.update(task, description="Created default mcp.json")
        
        (manager.config_dir / "mcp.json").write_text(json.dumps(mcp_data, indent=2))
        
        progress.update(task, description="[green]Done![/green]")
    
    console.print()
    console.print(Panel(
        f"[bold green]Setup complete![/bold green]\n\n"
        f"Configuration saved to:\n"
        f"  {manager.config_dir}\n\n"
        f"Next steps:\n"
        f"  1. Start PostgreSQL: [cyan]docker compose up -d db[/cyan]\n"
        f"  2. Verify setup: [cyan]ah doctor[/cyan]\n"
        f"  3. Start the bot: [cyan]ah start[/cyan]",
        expand=False,
    ))
    
    return config


def _run_oneshot(
    telegram_token: str,
    db_url: str,
    ollama_url: str,
    ollama_model: str,
    env: str,
    user_ids: Optional[str],
    force: bool,
) -> Optional[HarnessConfig]:
    """Run one-shot configuration."""
    manager = ConfigManager()
    
    # Check if config already exists
    if manager.exists() and not force:
        console.print("[red]Configuration already exists. Use --force to overwrite.[/red]")
        raise typer.Exit(1)
    
    # Parse user IDs
    allowed_ids = []
    if user_ids:
        allowed_ids = [int(x.strip()) for x in user_ids.split(",") if x.strip().isdigit()]
    
    # Create config
    config = HarnessConfig(
        env=env,
        telegram={"token": telegram_token, "allowed_user_ids": allowed_ids},
        database={"url": db_url},
        llm={"model": ollama_model, "api_base": ollama_url},
    )
    
    # Save
    manager.save(config)
    
    # Create default soul.md and mcp.json
    existing_configs = _find_existing_configs()
    
    if "soul" in existing_configs:
        soul_content = _convert_soul_yaml_to_md(existing_configs["soul"])
    else:
        soul_content = DEFAULT_SOUL_MD
    (manager.config_dir / "soul.md").write_text(soul_content)
    
    if "mcp" in existing_configs:
        mcp_data = _convert_mcp_yaml_to_json(existing_configs["mcp"])
    else:
        mcp_data = DEFAULT_MCP
    (manager.config_dir / "mcp.json").write_text(json.dumps(mcp_data, indent=2))
    
    console.print(f"[green]Configuration saved to {manager.config_dir}[/green]")
    return config


def init_command(
    telegram_token: Optional[str] = typer.Option(
        None,
        "--telegram-token",
        "-t",
        help="Telegram bot token from @BotFather.",
    ),
    db_url: Optional[str] = typer.Option(
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
    user_ids: Optional[str] = typer.Option(
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
        # Check if we have enough for one-shot mode
        if telegram_token and db_url:
            _run_oneshot(
                telegram_token=telegram_token,
                db_url=db_url,
                ollama_url=ollama_url,
                ollama_model=ollama_model,
                env=env,
                user_ids=user_ids,
                force=force,
            )
        else:
            # Run wizard
            existing_configs = _find_existing_configs()
            _run_wizard(force=force, existing_configs=existing_configs)
    except KeyboardInterrupt:
        console.print("\n[yellow]Setup cancelled.[/yellow]")
        raise typer.Exit(0)
