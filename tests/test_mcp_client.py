"""Tests for MCPClient."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from harness.providers.mcp_client import MCPClient, MCPTool, MCPToolResult


# ---------------------------------------------------------------------------
# MCPClient — without a server
# ---------------------------------------------------------------------------


def test_mcp_client_requires_command_or_url():
    with pytest.raises(ValueError, match="server_command"):
        MCPClient()


@pytest.mark.asyncio
async def test_mcp_client_list_tools_returns_empty_when_no_session():
    """When connection fails, list_tools should return [] gracefully."""
    client = MCPClient(server_command=["nonexistent_binary"])
    # Simulate connection failure by keeping _session None
    async with client:
        tools = await client.list_tools()
    assert tools == []


@pytest.mark.asyncio
async def test_mcp_client_call_tool_returns_stub_when_no_session():
    client = MCPClient(server_command=["nonexistent_binary"])
    async with client:
        result = await client.call_tool("some_tool", {"arg": "val"})
    assert result.is_error is True
    assert "unavailable" in result.content.lower()


def test_mcp_client_as_llm_tools_converts_correctly():
    client = MCPClient(server_command=["echo"])
    tools = [
        MCPTool(
            name="search",
            description="Search for things",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        )
    ]
    defs = client.as_llm_tools(tools)
    assert len(defs) == 1
    assert defs[0]["function"]["name"] == "search"
    assert defs[0]["type"] == "function"


# ---------------------------------------------------------------------------
# MCPTool and MCPToolResult
# ---------------------------------------------------------------------------


def test_mcp_tool_creation():
    """Test MCPTool dataclass."""
    tool = MCPTool(
        name="search",
        description="Search the web",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )
    
    assert tool.name == "search"
    assert tool.description == "Search the web"
    assert "q" in tool.input_schema["properties"]


def test_mcp_tool_result_success():
    """Test successful MCPToolResult."""
    result = MCPToolResult(content="Search results: ...")
    
    assert result.content == "Search results: ..."
    assert result.is_error is False


def test_mcp_tool_result_error():
    """Test error MCPToolResult."""
    result = MCPToolResult(content="Connection failed", is_error=True)
    
    assert result.content == "Connection failed"
    assert result.is_error is True


# ---------------------------------------------------------------------------
# MCPClient — tool conversion
# ---------------------------------------------------------------------------


def test_as_llm_tools_handles_empty_list():
    """Empty tool list returns empty definitions."""
    client = MCPClient(server_command=["echo"])
    defs = client.as_llm_tools([])
    assert defs == []


def test_as_llm_tools_preserves_schema():
    """Tool schema is preserved in conversion."""
    client = MCPClient(server_command=["echo"])
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["query"],
    }
    tools = [MCPTool(name="search", description="Search API", input_schema=schema)]
    
    defs = client.as_llm_tools(tools)
    
    assert defs[0]["function"]["parameters"] == schema
    assert defs[0]["function"]["description"] == "Search API"
