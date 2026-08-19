"""
Configuration management for Agent Harness.

This package handles:
- Loading/saving configuration from ~/.agent-harness/
- Pydantic schemas for validation
- Default configuration templates
- Environment variable overrides

Usage::

    from harness.config import ConfigManager, get_config_dir
    
    # Get config directory path
    config_dir = get_config_dir()
    
    # Load configuration
    manager = ConfigManager()
    config = manager.load()
    
    # Save configuration
    manager.save(config)
"""
from harness.config.loader import ConfigManager, get_config_dir, get_harness_home
from harness.config.schema import (
    HarnessConfig,
    TelegramConfig,
    DatabaseConfig,
    LLMConfig,
    MCPServerConfig,
)

__all__ = [
    "ConfigManager",
    "get_config_dir",
    "get_harness_home",
    "HarnessConfig",
    "TelegramConfig",
    "DatabaseConfig",
    "LLMConfig",
    "MCPServerConfig",
]
