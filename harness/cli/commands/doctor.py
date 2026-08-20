"""
ah doctor — Health check for all components.

Verifies:
- Configuration files (config.json, soul.md, mcp.json)
- PostgreSQL connection
- Ollama availability and model
- Soul file validity
- Skills loading
- MCP servers syntax
- Directory permissions
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from collections.abc import Callable

import typer
import yaml
from rich import box
from rich.console import Console
from rich.table import Table

from harness.config import ConfigManager, get_harness_home
from harness.core.exceptions import BOUNDARY_ERRORS

console = Console()


class HealthCheck:
    """Represents a single health check."""

    def __init__(
        self,
        name: str,
        check_fn: Callable[[], tuple[bool, str]],
    ) -> None:
        self.name = name
        self.check_fn = check_fn
        self.passed: bool | None = None
        self.details: str = ""

    def run(self) -> None:
        """Run the health check."""
        try:
            self.passed, self.details = self.check_fn()
        except BOUNDARY_ERRORS as e:
            self.passed = False
            self.details = f"Error: {e}"


def _check_config_dir() -> tuple[bool, str]:
    """Check if config directory exists and is writable."""
    config_dir = get_harness_home()

    if not config_dir.exists():
        return False, f"Not found: {config_dir}"

    if not os.access(config_dir, os.W_OK):
        return False, f"Not writable: {config_dir}"

    return True, str(config_dir)


def _check_config_file() -> tuple[bool, str]:
    """Check if config.json exists and is valid."""
    manager = ConfigManager()

    if not manager.config_file.exists():
        return False, "Not found. Run: ah init"

    try:
        config = manager.load()
        return True, f"Loaded ({config.env} mode)"
    except BOUNDARY_ERRORS as e:
        return False, f"Invalid: {e}"


def _check_telegram() -> tuple[bool, str]:
    """Check Telegram configuration."""
    manager = ConfigManager()
    config = manager.load()

    token = config.telegram.token
    if not token:
        return False, "Token not configured"

    # Basic format validation (bot tokens are like "123456:ABC-DEF")
    if ":" not in token or len(token) < 20:
        return False, "Token format looks invalid"

    user_count = len(config.telegram.allowed_user_ids)
    if user_count == 0:
        return False, "Token set, but no allowed users"

    return True, f"Token set, {user_count} allowed user(s)"


def _check_database() -> tuple[bool, str]:
    """Check PostgreSQL connection."""
    manager = ConfigManager()
    config = manager.load()

    db_url = config.database.url
    if not db_url:
        return False, "Database URL not configured"

    # Try to connect
    try:
        import asyncpg

        async def _ping() -> bool:
            try:
                conn = await asyncpg.connect(dsn=db_url, timeout=5)
                await conn.close()
                return True
            except (OSError, TimeoutError, ConnectionError, ValueError, RuntimeError):
                return False

        reachable = asyncio.run(_ping())

        if reachable:
            # Redact password for display
            if "@" in db_url:
                display_url = db_url.split("@")[1]
            else:
                display_url = db_url
            return True, f"Connected to {display_url}"
        return False, "Connection failed"

    except ImportError:
        return False, "asyncpg not installed"


def _check_ollama() -> tuple[bool, str]:
    """Check Ollama availability."""
    manager = ConfigManager()
    config = manager.load()

    api_base = config.llm.api_base
    model = config.llm.model

    try:
        url = api_base.rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())

        available_models = [m["name"] for m in data.get("models", [])]

        # Extract model name from LiteLLM format (ollama_chat/llama3.1 -> llama3.1)
        model_name = model.replace("ollama_chat/", "").replace("ollama/", "")

        if model_name in available_models:
            return True, f"{model_name} available ({len(available_models)} models)"
        else:
            return (
                False,
                f"{model_name} not found. Available: {', '.join(available_models[:3])}",
            )

    except (OSError, TimeoutError, json.JSONDecodeError, ValueError, KeyError):
        return False, f"Cannot reach {api_base}"


def _check_soul() -> tuple[bool, str]:
    """Check soul file exists and is valid."""
    manager = ConfigManager()
    soul_path = manager.soul_file

    if not soul_path.exists():
        return False, f"Not found: {soul_path.name}"

    try:
        content = soul_path.read_text()

        if soul_path.suffix == ".md":
            # Parse frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = yaml.safe_load(parts[1])
                    name = frontmatter.get("name", "unknown")
                    return True, f"soul.md ({name})"
            return False, "Invalid frontmatter format"

        if soul_path.suffix == ".yaml":
            data = yaml.safe_load(content)
            name = data.get("name", "unknown")
            return True, f"soul.yaml ({name})"

        return False, f"Unknown format: {soul_path.suffix}"

    except (OSError, yaml.YAMLError, TypeError, AttributeError, KeyError) as e:
        return False, f"Parse error: {e}"


def _check_skills() -> tuple[bool, str]:
    """Check skills loading."""
    try:
        from harness.skills.registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_builtin_skills()

        builtin_count = len(registry)

        # External skills are optional; report zero when loading fails.
        try:
            external_count = registry.load_external_skills()
        except BOUNDARY_ERRORS as exc:
            return True, f"{builtin_count} builtin, 0 external (load error: {exc})"

        return True, f"{builtin_count} builtin, {external_count} external"

    except BOUNDARY_ERRORS as e:
        return False, f"Load error: {e}"


def _check_mcp() -> tuple[bool, str]:
    """Check MCP configuration."""
    manager = ConfigManager()
    mcp_path = manager.mcp_file

    if not mcp_path.exists():
        return True, "No MCP servers configured"

    try:
        with mcp_path.open() as f:
            data = json.load(f)

        servers = data.get("servers", [])
        enabled = [s for s in servers if s.get("enabled", True)]

        if not servers:
            return True, "No servers defined"

        return True, f"{len(enabled)} server(s) enabled"

    except (OSError, json.JSONDecodeError, TypeError, AttributeError, KeyError) as e:
        return False, f"Parse error: {e}"


def _check_permissions() -> tuple[bool, str]:
    """Check directory permissions."""
    manager = ConfigManager()

    dirs_to_check = [
        manager.config_dir,
        manager.skills_dir,
        manager.logs_dir,
        manager.config_dir / "data",
        manager.config_dir / "pid",
    ]

    issues = []
    for d in dirs_to_check:
        if d.exists() and not os.access(d, os.W_OK):
            issues.append(d.name)

    if issues:
        return False, f"Not writable: {', '.join(issues)}"

    return True, "All directories writable"


def doctor_command(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed information for each check.",
    ),
) -> None:
    """
    Health check — verify all Agent Harness components.

    Checks configuration, database, Ollama, soul, skills, MCP, and permissions.
    """
    console.print()
    console.print("[bold cyan]Agent Harness — Doctor[/bold cyan]")
    console.print()

    # Define all checks
    checks = [
        HealthCheck("Config Directory", _check_config_dir),
        HealthCheck("config.json", _check_config_file),
        HealthCheck("Telegram", _check_telegram),
        HealthCheck("PostgreSQL", _check_database),
        HealthCheck("Ollama", _check_ollama),
        HealthCheck("Soul", _check_soul),
        HealthCheck("Skills", _check_skills),
        HealthCheck("MCP Servers", _check_mcp),
        HealthCheck("Permissions", _check_permissions),
    ]

    # Run all checks
    for check in checks:
        check.run()

    # Display results
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("Component", style="bold", width=18)
    table.add_column("Status", width=8)
    table.add_column("Details")

    all_passed = True
    for check in checks:
        if check.passed:
            status = "[bold green]OK[/bold green]"
        else:
            status = "[bold red]FAIL[/bold red]"
            all_passed = False

        table.add_row(check.name, status, check.details)

    console.print(table)
    console.print()

    # Summary
    passed_count = sum(1 for c in checks if c.passed)
    total_count = len(checks)

    if all_passed:
        console.print(f"[bold green]All {total_count} checks passed![/bold green]")
        console.print()
        console.print("Ready to start: [cyan]ah start[/cyan]")
    else:
        console.print(f"[bold yellow]{passed_count}/{total_count} checks passed.[/bold yellow]")
        console.print()
        console.print("Fix the issues above, then run [cyan]ah doctor[/cyan] again.")
        console.print("For initial setup, run: [cyan]ah init[/cyan]")

    console.print()
