"""
MCPClient — async client for Model Context Protocol servers.

Supports both stdio (subprocess) and SSE (HTTP) transports.
Each agent creates its own MCPClient instance; the client is used as an
async context manager to manage the connection lifecycle.

Usage (stdio)::

    async with MCPClient(server_command=["python", "-m", "my_mcp_server"]) as client:
        tools = await client.list_tools()
        result = await client.call_tool("search", {"query": "python"})

Usage (SSE)::

    async with MCPClient(server_url="http://localhost:8000/sse") as client:
        ...
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class MCPTool:
    """A tool advertised by an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPToolResult:
    """Result of a tool call."""

    content: str
    is_error: bool = False


class MCPClient:
    """
    Async context-manager wrapper around the MCP Python SDK v2.

    Falls back gracefully when the MCP SDK is not installed or the server
    command is not configured — this allows development without a live MCP
    server.
    """

    def __init__(
        self,
        server_command: list[str] | None = None,
        server_url: str | None = None,
    ) -> None:
        if not server_command and not server_url:
            raise ValueError(
                "Provide either server_command (stdio) or server_url (SSE)."
            )

        self._server_command = server_command
        self._server_url = server_url
        self._session: Any = None
        self._cm: Any = None  # the transport context manager
        self._tools_cache: list[MCPTool] | None = None

    async def __aenter__(self) -> MCPClient:
        try:
            await self._connect()
        except Exception as exc:
            log.warning("mcp_connect_failed", error=str(exc))
            # Stay functional in stub mode — callers check _session is None
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._disconnect()

    async def _connect(self) -> None:
        from mcp import ClientSession

        if self._server_command:
            from mcp.client.stdio import StdioServerParameters, stdio_client

            params = StdioServerParameters(
                command=self._server_command[0], args=self._server_command[1:]
            )
            self._cm = stdio_client(params)
        else:
            from mcp.client.sse import sse_client

            self._cm = sse_client(url=self._server_url)

        read, write = await self._cm.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()
        log.info("mcp_connected", transport="stdio" if self._server_command else "sse")

    async def _disconnect(self) -> None:
        try:
            if self._session:
                await self._session.__aexit__(None, None, None)
            if self._cm:
                await self._cm.__aexit__(None, None, None)
        except Exception as exc:
            log.warning("mcp_disconnect_error", error=str(exc))
        self._session = None
        self._cm = None
        self._tools_cache = None

    async def list_tools(self) -> list[MCPTool]:
        """Return the tools exposed by the connected MCP server."""
        if self._session is None:
            return []

        if self._tools_cache is not None:
            return self._tools_cache

        result = await self._session.list_tools()
        tools = [
            MCPTool(
                name=t.name,
                description=t.description or "",
                input_schema=t.inputSchema or {},
            )
            for t in (result.tools or [])
        ]
        self._tools_cache = tools
        log.debug("mcp_tools_listed", count=len(tools))
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        """Call a tool on the MCP server and return its result."""
        if self._session is None:
            return MCPToolResult(
                content=f"[MCP unavailable] Tool '{name}' not executed.",
                is_error=True,
            )

        t0 = time.monotonic()
        try:
            result = await self._session.call_tool(name, arguments)
        except Exception as exc:
            log.error("mcp_tool_call_failed", tool=name, error=str(exc))
            return MCPToolResult(
                content=f"Error calling '{name}': {exc}", is_error=True
            )

        latency_ms = int((time.monotonic() - t0) * 1000)

        # Concatenate all text content blocks
        content_parts: list[str] = []
        for block in result.content or []:
            if hasattr(block, "text"):
                content_parts.append(block.text)
            else:
                content_parts.append(str(block))

        content = "\n".join(content_parts) if content_parts else "(no output)"
        is_error = bool(result.isError)

        log.debug(
            "mcp_tool_called", tool=name, latency_ms=latency_ms, is_error=is_error
        )
        return MCPToolResult(content=content, is_error=is_error)

    def as_llm_tools(self, tools: list[MCPTool]) -> list[dict[str, Any]]:
        """
        Convert a list of :class:`MCPTool` into OpenAI-compatible tool definitions.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema
                    or {
                        "type": "object",
                        "properties": {},
                    },
                },
            }
            for t in tools
        ]
