"""
Configuration management for Agent Harness.

This package handles:
- Loading/saving configuration from ~/.agent-harness/
- Pydantic schemas for validation
- Default configuration templates
- Environment variable overrides

Usage::

    from harness.config import ConfigManager, get_config_dir

    config_dir = get_config_dir()

    manager = ConfigManager()
    config = manager.load()

    manager.save(config)
"""

from harness.config.loader import ConfigManager, get_config_dir, get_harness_home
from harness.config.schema import (
    DatabaseConfig,
    HarnessConfig,
    LLMConfig,
    MCPServerConfig,
    TelegramConfig,
)

__all__ = [
    "ConfigManager",
    "DatabaseConfig",
    "HarnessConfig",
    "LLMConfig",
    "MCPServerConfig",
    "TelegramConfig",
    "get_config_dir",
    "get_harness_home",
]
