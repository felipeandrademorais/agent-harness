"""
Agent Harness — entry point.

Bootstraps all components and starts the Telegram bot:
  1. Configure structured logging (structlog → JSON)
  2. Connect to PostgreSQL and run migrations
  3. Build LLMProvider (Ollama by default)
  4. Load Soul (personality and behaviors)
  5. Load Skills from config
  6. Load MCP servers from config
  7. Create PrimaryAgent with Soul + Skills + MCP
  8. Wire Agent Factory for spawning sub-agents
  9. Start TelegramChannel with graceful shutdown on SIGINT/SIGTERM
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import structlog
import yaml

# ---------------------------------------------------------------------------
# Logging setup — must happen before any module imports that use structlog
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if sys.stderr.isatty() else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    # Keep third-party loggers quiet
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)


_configure_logging()
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Component imports (after logging is configured)
# ---------------------------------------------------------------------------

from harness.channels.telegram import TelegramChannel
from harness.core.dispatcher import Dispatcher
from harness.agents.primary import PrimaryAgent
from harness.agents.factory import AgentFactory
from harness.providers.llm_provider import LLMProvider
from harness.providers.mcp_manager import MCPManager
from harness.memory.repository import ConversationRepository
from harness.skills.registry import SkillRegistry
from harness.soul import load_soul


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _load_yaml(path: str | Path) -> dict:
    """Load YAML configuration file."""
    config_path = Path(path)
    if not config_path.exists():
        log.warning("config_not_found", path=str(config_path))
        return {}

    with config_path.open() as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    log.info("harness_starting")

    # --- Validate required env vars ---
    _require_env("TELEGRAM_TOKEN")
    _require_env("DATABASE_URL")

    # --- Memory ---
    database_url = os.environ["DATABASE_URL"]
    memory = ConversationRepository()
    await memory.connect(database_url)
    await memory.run_migrations()
    log.info("database_ready", url=_redact(database_url))

    # --- Seed allowed users from ALLOWED_USER_IDS env var ---
    await _seed_allowed_users(memory)

    # --- LLM Provider ---
    llm = LLMProvider.from_env()
    log.info("llm_ready", model=llm.model, api_base=llm.api_base)

    # --- Soul (personality and behaviors) ---
    soul = load_soul("config/soul.yaml")
    log.info(
        "soul_ready",
        name=soul.name,
        mood=soul.mood,
        language=soul.language,
    )

    # --- Skills ---
    skills = SkillRegistry()
    skills.load_from_config("config/skills.yaml")
    
    # Load external (user-defined) skills
    external_count = skills.load_external_skills()
    
    log.info(
        "skills_ready",
        count=len(skills),
        names=[s.name for s in skills.list_all()],
        external=external_count,
    )

    # --- MCP Manager ---
    mcp_manager = MCPManager()
    mcp_config = _load_yaml("config/mcp.yaml")
    mcp_servers = mcp_config.get("servers", [])
    if mcp_servers:
        await mcp_manager.connect_all(mcp_servers)
        log.info(
            "mcp_ready",
            servers=mcp_manager.list_servers(),
            total_tools=mcp_manager.total_tools,
        )
    else:
        log.info("mcp_no_servers_configured")

    # --- PrimaryAgent ---
    primary = PrimaryAgent(
        llm_provider=llm,
        soul=soul,
        skills=skills,
        memory=memory,
        mcp_manager=mcp_manager if mcp_manager.total_tools > 0 else None,
    )

    # --- Agent Factory (for spawning sub-agents) ---
    factory = AgentFactory(
        llm=llm,
        skills=skills,
        soul=soul,
    )
    primary.set_factory(factory)
    log.info("factory_ready")

    # --- Telegram Channel ---
    token = os.environ["TELEGRAM_TOKEN"]
    channel = TelegramChannel(token=token, memory=memory, skills=skills)

    # --- Dispatcher ---
    dispatcher = Dispatcher(primary=primary, channel=channel, memory=memory)
    channel.set_handler(dispatcher.handle_message)

    # --- Graceful shutdown ---
    stop_event = asyncio.Event()

    def _on_signal(*_: object) -> None:
        log.info("shutdown_requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    log.info(
        "harness_ready",
        channel="telegram",
        skills=len(skills),
        mcp_tools=mcp_manager.total_tools,
    )

    try:
        await channel.start(stop_event)
    finally:
        # Cleanup
        await mcp_manager.disconnect_all()
        await memory.close()
        log.info("harness_stopped")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: Required environment variable '{name}' is not set.", file=sys.stderr)
        print("Copy .env.example to .env and fill in the values.", file=sys.stderr)
        sys.exit(1)
    return value


def _redact(url: str) -> str:
    """Hide the password portion of a DB URL for logging."""
    if "@" in url:
        scheme_user, rest = url.split("@", 1)
        if ":" in scheme_user:
            prefix = scheme_user.rsplit(":", 1)[0]
            return f"{prefix}:***@{rest}"
    return url


async def _seed_allowed_users(memory: ConversationRepository) -> None:
    """
    Seed the allowed_users table from the ALLOWED_USER_IDS env var.

    Format: comma-separated integer user IDs.
    Example: ALLOWED_USER_IDS=123456789,987654321
    """
    raw = os.environ.get("ALLOWED_USER_IDS", "").strip()
    if not raw:
        log.warning("no_allowed_users_configured", hint="Set ALLOWED_USER_IDS in .env")
        return

    count = 0
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            await memory.add_allowed_user(int(part))
            count += 1

    log.info("allowed_users_seeded", count=count)


if __name__ == "__main__":
    asyncio.run(main())
