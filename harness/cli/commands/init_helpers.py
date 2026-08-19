"""Helpers for the `ah init` setup wizard."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from harness.config import ConfigManager, HarnessConfig
from harness.config.defaults import DEFAULT_MCP, DEFAULT_SOUL_MD

console = Console()

_OLLAMA_ERRORS = (OSError, TimeoutError, json.JSONDecodeError, urllib.error.URLError)


@dataclass
class OneshotOptions:
    """Options for one-shot (non-interactive) init."""

    telegram_token: str
    db_url: str
    ollama_url: str
    ollama_model: str
    env: str
    user_ids: str | None = None
    force: bool = False


def list_ollama_models(base_url: str) -> list[str]:
    """Fetch available Ollama models."""
    try:
        url = base_url.rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
        return [m["name"] for m in data.get("models", [])]
    except _OLLAMA_ERRORS:
        return []


def find_existing_configs() -> dict[str, Path]:
    """Find existing config files in ./config/ directory."""
    found: dict[str, Path] = {}
    config_dir = Path.cwd() / "config"

    if config_dir.exists():
        if (config_dir / "soul.yaml").exists():
            found["soul"] = config_dir / "soul.yaml"
        if (config_dir / "mcp.yaml").exists():
            found["mcp"] = config_dir / "mcp.yaml"
        if (config_dir / "skills.yaml").exists():
            found["skills"] = config_dir / "skills.yaml"

    env_file = Path.cwd() / ".env"
    if env_file.exists():
        found["env"] = env_file

    return found


def load_env_file(path: Path) -> dict[str, str]:
    """Load values from a .env file."""
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def convert_soul_yaml_to_md(yaml_path: Path) -> str:
    """Convert soul.yaml to soul.md format."""
    with yaml_path.open() as f:
        data = yaml.safe_load(f) or {}

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

    body = data.get("system_prompt_template", "")
    if not body:
        caps = data.get("capabilities")
        capabilities = caps.get("description", "") if isinstance(caps, dict) else ""
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

    frontmatter_yaml = yaml.dump(
        frontmatter, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{frontmatter_yaml}---\n\n{body}"


def convert_mcp_yaml_to_json(yaml_path: Path) -> dict:
    """Convert mcp.yaml to mcp.json format."""
    with yaml_path.open() as f:
        data = yaml.safe_load(f) or {}

    servers = []
    for server in data.get("servers", []):
        servers.append(
            {
                "name": server.get("name", "unknown"),
                "type": server.get("type", "stdio"),
                "command": server.get("command", []),
                "url": server.get("url"),
                "env": server.get("env", {}),
                "enabled": True,
            }
        )

    return {"servers": servers}


def prompt_telegram(env_values: dict[str, str]) -> tuple[str, list[int]]:
    """Wizard step: Telegram bot token and allowed user IDs."""
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
    return telegram_token, user_ids


def prompt_database(env_values: dict[str, str]) -> str:
    """Wizard step: PostgreSQL database URL."""
    console.print()
    console.rule("[bold]Step 2/4 — PostgreSQL Database[/bold]")
    console.print("  The harness uses PostgreSQL for conversation history.")
    console.print()

    default_db = env_values.get(
        "DATABASE_URL", "postgresql://harness:harness@localhost:5455/harness"
    )
    use_docker = typer.confirm("Use Docker PostgreSQL on port 5455?", default=True)

    if use_docker:
        console.print("[dim]  Run: docker compose up -d db[/dim]")
        return "postgresql://harness:harness@localhost:5455/harness"

    return typer.prompt("Database URL", default=default_db)


def prompt_ollama(env_values: dict[str, str]) -> tuple[str, str]:
    """Wizard step: Ollama URL and model."""
    console.print()
    console.rule("[bold]Step 3/4 — Ollama LLM[/bold]")
    console.print("  Ollama provides local LLM inference.")
    console.print()

    default_ollama_url = env_values.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_url = typer.prompt("Ollama URL", default=default_ollama_url)

    models = list_ollama_models(ollama_url)
    if models:
        console.print(
            f"[green]  Found {len(models)} model(s): {', '.join(models[:5])}"
            f"{'...' if len(models) > 5 else ''}[/green]"
        )
        default_model = env_values.get("OLLAMA_MODEL", f"ollama_chat/{models[0]}")
    else:
        console.print(
            "[yellow]  Could not connect to Ollama. Make sure it's running.[/yellow]"
        )
        default_model = env_values.get("OLLAMA_MODEL", "ollama_chat/llama3.1")

    ollama_model = typer.prompt("Model (LiteLLM format)", default=default_model)
    return ollama_url, ollama_model


def prompt_environment() -> str:
    """Wizard step: environment (dev/prod)."""
    console.print()
    console.rule("[bold]Step 4/4 — Environment[/bold]")
    console.print("  dev = foreground mode (logs to stdout)")
    console.print("  prod = daemon mode (background with heartbeat)")
    console.print()

    env = typer.prompt("Environment", default="dev")
    if env not in ("dev", "prod"):
        console.print("[yellow]Invalid environment, using 'dev'.[/yellow]")
        return "dev"
    return env


def save_wizard_artifacts(
    manager: ConfigManager,
    config: HarnessConfig,
    *,
    migrate: bool,
    existing_configs: dict[str, Path],
) -> None:
    """Persist config.json, soul.md, and mcp.json from wizard choices."""
    console.print()
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Saving configuration...", total=None)

        manager.save(config)
        progress.update(task, description="Saved config.json")

        if migrate and "soul" in existing_configs:
            soul_content = convert_soul_yaml_to_md(existing_configs["soul"])
            progress.update(task, description="Migrated soul.yaml → soul.md")
        else:
            soul_content = DEFAULT_SOUL_MD
            progress.update(task, description="Created default soul.md")

        (manager.config_dir / "soul.md").write_text(soul_content)

        if migrate and "mcp" in existing_configs:
            mcp_data = convert_mcp_yaml_to_json(existing_configs["mcp"])
            progress.update(task, description="Migrated mcp.yaml → mcp.json")
        else:
            mcp_data = DEFAULT_MCP
            progress.update(task, description="Created default mcp.json")

        (manager.config_dir / "mcp.json").write_text(json.dumps(mcp_data, indent=2))
        progress.update(task, description="[green]Done![/green]")


def print_setup_complete(config_dir: Path) -> None:
    """Print the post-setup success panel."""
    console.print()
    console.print(
        Panel(
            f"[bold green]Setup complete![/bold green]\n\n"
            f"Configuration saved to:\n"
            f"  {config_dir}\n\n"
            f"Next steps:\n"
            f"  1. Start PostgreSQL: [cyan]docker compose up -d db[/cyan]\n"
            f"  2. Verify setup: [cyan]ah doctor[/cyan]\n"
            f"  3. Start the bot: [cyan]ah start[/cyan]",
            expand=False,
        )
    )
