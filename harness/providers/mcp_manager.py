"""
MCPManager — manages multiple MCP server connections.

Provides a unified interface for the Orchestrator to access tools from
multiple MCP servers (filesystem, web search, shell, etc.) without needing
to create separate agents for each capability.

Usage::

    manager = MCPManager()
    await manager.connect_all([
        {"name": "filesystem", "type": "stdio", "command": ["npx", "...", "/path"]},
        {"name": "web", "type": "sse", "url": "http://localhost:8080/sse"},
    ])

    tools = await manager.list_all_tools()  # OpenAI-compatible format
    result = await manager.call_tool("read_file", {"path": "/etc/hosts"})

    await manager.disconnect_all()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from harness.core.exceptions import BOUNDARY_ERRORS
from harness.providers.mcp_client import MCPClient, MCPToolResult

log = structlog.get_logger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    type: str  # "stdio" or "sse"
    command: list[str] | None = None  # for stdio
    url: str | None = None  # for sse
    env: dict[str, str] = field(default_factory=dict)  # environment variables


class MCPManager:
    """
    Manages connections to multiple MCP servers and provides a unified
    tool interface for the Orchestrator.
    """

    def __init__(self) -> None:
        # server_name → MCPClient
        self._clients: dict[str, MCPClient] = {}
        # tool_name → server_name (for routing tool calls)
        self._tool_routing: dict[str, str] = {}
        # Cached tools in OpenAI format
        self._tools_cache: list[dict[str, Any]] | None = None

    async def connect_all(self, configs: list[dict[str, Any]]) -> None:
        """
        Connect to all configured MCP servers.

        Each config dict should have:
          - name: unique identifier for this server
          - type: "stdio" or "sse"
          - command: list[str] for stdio
          - url: str for sse
          - env: optional dict of environment variables
        """
        if not configs:
            log.info("mcp_manager_no_servers_configured")
            return

        for cfg in configs:
            server_config = MCPServerConfig(
                name=cfg.get("name", "unnamed"),
                type=cfg.get("type", "stdio"),
                command=cfg.get("command"),
                url=cfg.get("url"),
                env=cfg.get("env", {}),
            )
            await self._connect_server(server_config)

        await self._build_tool_routing()

        log.info(
            "mcp_manager_ready",
            servers=list(self._clients.keys()),
            total_tools=len(self._tool_routing),
        )

    async def _connect_server(self, config: MCPServerConfig) -> None:
        """Connect to a single MCP server."""
        try:
            if config.type == "stdio":
                if not config.command:
                    log.warning("mcp_server_missing_command", name=config.name)
                    return
                client = MCPClient(server_command=config.command)
            elif config.type == "sse":
                if not config.url:
                    log.warning("mcp_server_missing_url", name=config.name)
                    return
                client = MCPClient(server_url=config.url)
            else:
                log.warning(
                    "mcp_server_unknown_type", name=config.name, type=config.type
                )
                return

            await client.__aenter__()
            self._clients[config.name] = client
            log.info("mcp_server_connected", name=config.name, type=config.type)

        except BOUNDARY_ERRORS as exc:
            log.error("mcp_server_connect_failed", name=config.name, error=str(exc))

    async def _build_tool_routing(self) -> None:
        """Build the tool_name → server_name routing table."""
        self._tool_routing.clear()
        self._tools_cache = None

        for server_name, client in self._clients.items():
            try:
                tools = await client.list_tools()
                for tool in tools:
                    if tool.name in self._tool_routing:
                        log.warning(
                            "mcp_tool_name_conflict",
                            tool=tool.name,
                            existing_server=self._tool_routing[tool.name],
                            new_server=server_name,
                        )
                        # Keep the first one registered
                        continue
                    self._tool_routing[tool.name] = server_name
            except BOUNDARY_ERRORS as exc:
                log.error("mcp_list_tools_failed", server=server_name, error=str(exc))

    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        for name, client in self._clients.items():
            try:
                await client.__aexit__(None, None, None)
                log.debug("mcp_server_disconnected", name=name)
            except BOUNDARY_ERRORS as exc:
                log.warning("mcp_server_disconnect_error", name=name, error=str(exc))

        self._clients.clear()
        self._tool_routing.clear()
        self._tools_cache = None
        log.info("mcp_manager_disconnected")

    async def list_all_tools(self) -> list[dict[str, Any]]:
        """
        Return all tools from all connected servers in OpenAI-compatible format.

        Results are cached until a reconnection occurs.
        """
        if self._tools_cache is not None:
            return self._tools_cache

        all_tools: list[dict[str, Any]] = []

        for server_name, client in self._clients.items():
            try:
                mcp_tools = await client.list_tools()
                openai_tools = client.as_llm_tools(mcp_tools)
                all_tools.extend(openai_tools)
            except BOUNDARY_ERRORS as exc:
                log.error("mcp_list_tools_failed", server=server_name, error=str(exc))

        self._tools_cache = all_tools
        return all_tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        """
        Call a tool by name, routing to the appropriate MCP server.

        Returns MCPToolResult with content and is_error flag.
        """
        server_name = self._tool_routing.get(name)
        if server_name is None:
            log.warning("mcp_tool_not_found", tool=name)
            return MCPToolResult(
                content=f"Tool '{name}' not found in any connected MCP server.",
                is_error=True,
            )

        client = self._clients.get(server_name)
        if client is None:
            log.error("mcp_server_not_connected", server=server_name, tool=name)
            return MCPToolResult(
                content=f"MCP server '{server_name}' is not connected.",
                is_error=True,
            )

        return await client.call_tool(name, arguments)

    def get_tool_server(self, tool_name: str) -> str | None:
        """Return the server name that provides a given tool."""
        return self._tool_routing.get(tool_name)

    def list_servers(self) -> list[str]:
        """Return names of all connected servers."""
        return list(self._clients.keys())

    def is_connected(self, server_name: str) -> bool:
        """Check if a specific server is connected."""
        return server_name in self._clients

    @property
    def total_tools(self) -> int:
        """Total number of tools available across all servers."""
        return len(self._tool_routing)

    def __repr__(self) -> str:
        return f"<MCPManager servers={list(self._clients.keys())} tools={self.total_tools}>"
