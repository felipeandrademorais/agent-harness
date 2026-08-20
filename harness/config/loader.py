"""
Configuration loader and manager for Agent Harness.

Handles:
- Auto-creation of ~/.agent-harness/ directory
- Loading/saving config.json and mcp.json
- Environment variable overrides
- Default configuration
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from harness.config.schema import HarnessConfig, MCPConfig
from harness.core.exceptions import BOUNDARY_ERRORS

log = structlog.get_logger(__name__)

# Default config directory
DEFAULT_CONFIG_DIR = "~/.agent-harness"
ENV_VAR_HOME = "AGENT_HARNESS_HOME"
_CONFIG_LOAD_ERRORS = (json.JSONDecodeError, ValidationError, *BOUNDARY_ERRORS)


def get_harness_home() -> Path:
    """
    Get the Agent Harness home directory.

    Priority:
    1. AGENT_HARNESS_HOME environment variable
    2. ~/.agent-harness/

    :returns: Path to the config directory.
    """
    env_home = os.environ.get(ENV_VAR_HOME)
    if env_home:
        return Path(env_home).expanduser().resolve()
    return Path(DEFAULT_CONFIG_DIR).expanduser().resolve()


def get_config_dir() -> Path:
    """Alias for get_harness_home() for backward compatibility."""
    return get_harness_home()


class ConfigManager:
    """
    Manages Agent Harness configuration files.

    Handles config.json, mcp.json, and directory structure.
    Auto-creates the config directory if it doesn't exist.

    Usage::

        manager = ConfigManager()
        config = manager.load()

        # Modify config
        config.env = "prod"

        manager.save(config)
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        """
        Initialize the ConfigManager.

        :param config_dir: Custom config directory. If None, uses get_harness_home().
        """
        self.config_dir = config_dir or get_harness_home()
        self._ensure_directory_structure()

    def _ensure_directory_structure(self) -> None:
        """Create the config directory structure if it doesn't exist."""
        directories = [
            self.config_dir,
            self.config_dir / "skills",
            self.config_dir / "logs",
            self.config_dir / "data",
            self.config_dir / "pid",
        ]

        for directory in directories:
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
                log.debug("created_directory", path=str(directory))

    @property
    def config_file(self) -> Path:
        """Path to config.json."""
        return self.config_dir / "config.json"

    @property
    def mcp_file(self) -> Path:
        """Path to mcp.json."""
        return self.config_dir / "mcp.json"

    @property
    def soul_file(self) -> Path:
        """Path to soul.md (or soul.yaml for legacy)."""
        md_path = self.config_dir / "soul.md"
        yaml_path = self.config_dir / "soul.yaml"

        if md_path.exists():
            return md_path
        if yaml_path.exists():
            return yaml_path
        return md_path  # Default to .md for new installations

    @property
    def pid_file(self) -> Path:
        """Path to the daemon PID file."""
        return self.config_dir / "pid" / "harness.pid"

    @property
    def skills_dir(self) -> Path:
        """Path to user skills directory."""
        return self.config_dir / "skills"

    @property
    def logs_dir(self) -> Path:
        """Path to logs directory."""
        return self.config_dir / "logs"

    def exists(self) -> bool:
        """Check if configuration exists."""
        return self.config_file.exists()

    def load(self) -> HarnessConfig:
        """
        Load configuration from config.json.

        If the file doesn't exist, returns default configuration.
        Environment variables can override config values.

        :returns: Loaded or default HarnessConfig.
        """
        if self.config_file.exists():
            try:
                with self.config_file.open() as f:
                    data = json.load(f)
                config = HarnessConfig.model_validate(data)
                log.info("config_loaded", path=str(self.config_file))
            except _CONFIG_LOAD_ERRORS as e:
                log.error("config_load_failed", path=str(self.config_file), error=str(e))
                config = HarnessConfig()
        else:
            log.info("config_not_found_using_defaults", path=str(self.config_file))
            config = HarnessConfig()

        # Apply environment variable overrides
        config = self._apply_env_overrides(config)

        return config

    def _apply_env_overrides(self, config: HarnessConfig) -> HarnessConfig:
        """
        Apply environment variable overrides to config.

        Supported env vars:
        - TELEGRAM_TOKEN -> telegram.token
        - ALLOWED_USER_IDS -> telegram.allowed_user_ids
        - DATABASE_URL -> database.url
        - OLLAMA_BASE_URL -> llm.api_base
        - OLLAMA_MODEL -> llm.model
        - OPENAI_API_KEY / ANTHROPIC_API_KEY -> llm.api_key
        - HARNESS_ENV -> env
        """
        # Telegram
        if token := os.environ.get("TELEGRAM_TOKEN"):
            config.telegram.token = token  # type: ignore

        if user_ids := os.environ.get("ALLOWED_USER_IDS"):
            ids = [int(x.strip()) for x in user_ids.split(",") if x.strip().isdigit()]
            if ids:
                config.telegram.allowed_user_ids = ids

        # Database
        if db_url := os.environ.get("DATABASE_URL"):
            config.database.url = db_url  # type: ignore

        # LLM
        if ollama_url := os.environ.get("OLLAMA_BASE_URL"):
            config.llm.api_base = ollama_url

        if model := os.environ.get("OLLAMA_MODEL"):
            config.llm.model = model

        if api_key := os.environ.get("OPENAI_API_KEY"):
            config.llm.provider = "openai"
            config.llm.api_key = api_key  # type: ignore
        elif api_key := os.environ.get("ANTHROPIC_API_KEY"):
            config.llm.provider = "anthropic"
            config.llm.api_key = api_key  # type: ignore

        # Environment
        if (env := os.environ.get("HARNESS_ENV")) and env in ("dev", "prod"):
            config.env = env

        return config

    def save(self, config: HarnessConfig) -> None:
        """
        Save configuration to config.json.

        :param config: Configuration to save.
        """
        self._ensure_directory_structure()

        # Convert to dict, handling SecretStr
        data = config.model_dump(mode="json")

        with self.config_file.open("w") as f:
            json.dump(data, f, indent=2)

        log.info("config_saved", path=str(self.config_file))

    def load_mcp(self) -> MCPConfig:
        """
        Load MCP servers configuration from mcp.json.

        :returns: Loaded or empty MCPConfig.
        """
        if self.mcp_file.exists():
            try:
                with self.mcp_file.open() as f:
                    data = json.load(f)
                return MCPConfig.model_validate(data)
            except _CONFIG_LOAD_ERRORS as e:
                log.error("mcp_config_load_failed", path=str(self.mcp_file), error=str(e))

        return MCPConfig()

    def save_mcp(self, mcp_config: MCPConfig) -> None:
        """
        Save MCP servers configuration to mcp.json.

        :param mcp_config: MCP configuration to save.
        """
        self._ensure_directory_structure()

        with self.mcp_file.open("w") as f:
            json.dump(mcp_config.model_dump(mode="json"), f, indent=2)

        log.info("mcp_config_saved", path=str(self.mcp_file))

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a config value by dot-notation key.

        :param key: Key in dot notation (e.g., "telegram.token", "llm.model").
        :param default: Default value if key not found.
        :returns: Config value or default.
        """
        config = self.load()

        parts = key.split(".")
        obj: Any = config

        for part in parts:
            if hasattr(obj, part):
                obj = getattr(obj, part)
            elif isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                return default

        return obj

    def set(self, key: str, value: Any) -> None:
        """
        Set a config value by dot-notation key.

        :param key: Key in dot notation (e.g., "telegram.token", "llm.model").
        :param value: Value to set.
        """
        config = self.load()

        parts = key.split(".")
        obj = config

        # Navigate to parent
        for part in parts[:-1]:
            if hasattr(obj, part):
                obj = getattr(obj, part)
            else:
                raise KeyError(f"Invalid config key: {key}")

        # Set value
        final_key = parts[-1]
        if hasattr(obj, final_key):
            setattr(obj, final_key, value)
        else:
            raise KeyError(f"Invalid config key: {key}")

        self.save(config)

    def to_dict(self, redact_secrets: bool = True) -> dict:
        """
        Convert configuration to dictionary.

        :param redact_secrets: If True, replace secret values with "***".
        :returns: Configuration as dictionary.
        """
        config = self.load()
        data = config.model_dump(mode="json")

        if redact_secrets:
            # Redact known secret fields
            telegram = data.get("telegram") or {}
            if telegram.get("token"):
                data["telegram"]["token"] = "***"
            database = data.get("database") or {}
            if database.get("url"):
                data["database"]["url"] = "***"
            llm = data.get("llm") or {}
            if llm.get("api_key"):
                data["llm"]["api_key"] = "***"

        return data
