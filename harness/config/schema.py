"""
Pydantic models for Agent Harness configuration.

These models define the structure of config.json and provide validation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TelegramConfig(BaseModel):
    """Telegram bot configuration."""

    model_config = {"validate_assignment": True}

    token: str | None = Field(
        default=None,
        description="Bot token from @BotFather",
    )
    allowed_user_ids: list[int] = Field(
        default_factory=list,
        description="List of Telegram user IDs allowed to use the bot",
    )


class DatabaseConfig(BaseModel):
    """PostgreSQL database configuration."""

    model_config = {"validate_assignment": True}

    url: str | None = Field(
        default=None,
        description="PostgreSQL connection URL",
    )
    pool_size: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Connection pool size",
    )


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    model_config = {"validate_assignment": True}

    provider: str = Field(
        default="ollama",
        description="LLM provider: ollama, openai, anthropic",
    )
    model: str = Field(
        default="ollama_chat/llama3.1",
        description="Model identifier (LiteLLM format)",
    )
    api_base: str = Field(
        default="http://localhost:11434",
        description="API base URL (for Ollama or custom endpoints)",
    )
    api_key: str | None = Field(
        default=None,
        description="API key (for OpenAI, Anthropic, etc.)",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    max_tokens: int = Field(
        default=4096,
        ge=1,
        description="Maximum tokens in response",
    )


class MCPServerConfig(BaseModel):
    """MCP server configuration."""

    name: str = Field(
        description="Server name for identification",
    )
    type: str = Field(
        default="stdio",
        description="Server type: stdio, http, websocket",
    )
    command: list[str] = Field(
        default_factory=list,
        description="Command to start the server (for stdio type)",
    )
    url: str | None = Field(
        default=None,
        description="Server URL (for http/websocket types)",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables for the server",
    )
    enabled: bool = Field(
        default=True,
        description="Whether the server is enabled",
    )


class ContextLimitsConfig(BaseModel):
    """Context window limits for different scenarios."""

    max_history_messages: int = Field(
        default=50,
        ge=1,
        description="Maximum number of history messages to keep",
    )
    max_context_tokens: int = Field(
        default=8192,
        ge=1,
        description="Maximum context tokens for the LLM",
    )
    truncation_strategy: str = Field(
        default="sliding_window",
        description="How to truncate: sliding_window, summarize",
    )


class DaemonConfig(BaseModel):
    """Daemon mode configuration."""

    heartbeat_interval: int = Field(
        default=60,
        ge=10,
        description="Heartbeat interval in seconds",
    )
    agent_timeout: int = Field(
        default=300,
        ge=30,
        description="Agent execution timeout in seconds",
    )
    telegram_notify_on_failure: bool = Field(
        default=True,
        description="Send Telegram notification if daemon dies",
    )
    telegram_admin_id: int | None = Field(
        default=None,
        description="Telegram user ID to notify on failure",
    )


class HarnessConfig(BaseModel):
    """
    Main configuration for Agent Harness.

    Stored in ~/.agent-harness/config.json
    """

    # Environment: dev or prod
    env: str = Field(
        default="dev",
        pattern="^(dev|prod)$",
        description="Environment: dev (foreground) or prod (daemon)",
    )

    # Sub-configurations
    telegram: TelegramConfig = Field(
        default_factory=TelegramConfig,
        description="Telegram bot settings",
    )
    database: DatabaseConfig = Field(
        default_factory=DatabaseConfig,
        description="PostgreSQL settings",
    )
    llm: LLMConfig = Field(
        default_factory=LLMConfig,
        description="LLM provider settings",
    )
    context_limits: ContextLimitsConfig = Field(
        default_factory=ContextLimitsConfig,
        description="Context window limits",
    )
    daemon: DaemonConfig = Field(
        default_factory=DaemonConfig,
        description="Daemon mode settings",
    )

    # Paths (relative to config dir)
    soul_file: str = Field(
        default="soul.md",
        description="Path to soul configuration file",
    )
    mcp_file: str = Field(
        default="mcp.json",
        description="Path to MCP servers configuration file",
    )
    skills_dir: str = Field(
        default="skills",
        description="Directory for user-installed skills",
    )
    logs_dir: str = Field(
        default="logs",
        description="Directory for log files",
    )
    data_dir: str = Field(
        default="data",
        description="Directory for runtime data",
    )

    def get_value(self, key: str) -> str | None:
        """
        Get the value of a field by dot notation key.

        :param key: Key in dot notation (e.g., "telegram.token", "database.url").
        :returns: Field value or None if not found.
        """
        parts = key.split(".")
        obj = self
        for part in parts[:-1]:
            obj = getattr(obj, part, None)
            if obj is None:
                return None

        return getattr(obj, parts[-1], None)


class MCPConfig(BaseModel):
    """
    MCP servers configuration.

    Stored in ~/.agent-harness/mcp.json
    """

    servers: list[MCPServerConfig] = Field(
        default_factory=list,
        description="List of MCP servers",
    )
