"""Tests for LLMProvider — mocks litellm.acompletion."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from harness.providers.llm_provider import LLMProvider, LLMProviderError, ToolCall

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_response(content="Hello!", tool_calls=None):
    """Build a minimal fake litellm response object."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls or []

    choice = MagicMock()
    choice.message = message

    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.total_tokens = 15

    raw = MagicMock()
    raw.choices = [choice]
    raw.usage = usage
    return raw


def _make_tool_call(id="tc1", name="my_tool", arguments='{"arg": "value"}'):
    tc = MagicMock()
    tc.id = id
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


# ---------------------------------------------------------------------------
# from_env
# ---------------------------------------------------------------------------


def test_from_env_reads_env_vars(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "ollama_chat/qwen2.5:14b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://myhost:11434")
    provider = LLMProvider.from_env()
    assert provider.model == "ollama_chat/qwen2.5:14b"
    assert provider.api_base == "http://myhost:11434"


def test_from_env_uses_defaults(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    provider = LLMProvider.from_env()
    assert provider.model == "ollama_chat/llama3.1"
    assert provider.api_base == "http://localhost:11434"


# ---------------------------------------------------------------------------
# complete — plain text response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_returns_content():
    provider = LLMProvider(
        model="ollama_chat/llama3.1", api_base="http://localhost:11434"
    )
    raw = _make_raw_response(content="I am an AI.")

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=raw):
        response = await provider.complete(
            [{"role": "user", "content": "Who are you?"}]
        )

    assert response.content == "I am an AI."
    assert response.tool_calls == []
    assert response.usage["total_tokens"] == 15


@pytest.mark.asyncio
async def test_complete_injects_api_base_for_ollama():
    provider = LLMProvider(
        model="ollama_chat/llama3.1", api_base="http://localhost:11434"
    )
    raw = _make_raw_response()

    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=raw
    ) as mock_call:
        await provider.complete([{"role": "user", "content": "test"}])

    call_kwargs = mock_call.call_args[1]
    assert call_kwargs.get("api_base") == "http://localhost:11434"


@pytest.mark.asyncio
async def test_complete_does_not_inject_api_base_for_openai():
    provider = LLMProvider(model="gpt-4o", api_base=None)
    raw = _make_raw_response()

    with patch(
        "litellm.acompletion", new_callable=AsyncMock, return_value=raw
    ) as mock_call:
        await provider.complete([{"role": "user", "content": "test"}])

    call_kwargs = mock_call.call_args[1]
    assert "api_base" not in call_kwargs


# ---------------------------------------------------------------------------
# complete — tool calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_parses_tool_calls():
    provider = LLMProvider(model="ollama_chat/llama3.1")
    tc = _make_tool_call(id="tc_1", name="search", arguments='{"query": "python"}')
    raw = _make_raw_response(content=None, tool_calls=[tc])

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=raw):
        response = await provider.complete(
            [{"role": "user", "content": "search python"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "description": "...",
                        "parameters": {},
                    },
                }
            ],
        )

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0] == ToolCall(
        id="tc_1", name="search", arguments={"query": "python"}
    )


@pytest.mark.asyncio
async def test_complete_handles_malformed_tool_call_arguments():
    provider = LLMProvider(model="ollama_chat/llama3.1")
    tc = _make_tool_call(arguments="not valid json")
    raw = _make_raw_response(content=None, tool_calls=[tc])

    with patch("litellm.acompletion", new_callable=AsyncMock, return_value=raw):
        response = await provider.complete([{"role": "user", "content": "x"}], tools=[])

    # Should not raise; raw string is preserved in a key
    assert response.tool_calls[0].arguments == {"raw": "not valid json"}


# ---------------------------------------------------------------------------
# complete — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_raises_llm_provider_error_on_exception():
    provider = LLMProvider(model="ollama_chat/llama3.1")

    with patch(
        "litellm.acompletion",
        new_callable=AsyncMock,
        side_effect=RuntimeError("connection refused"),
    ):
        with pytest.raises(LLMProviderError, match="connection refused"):
            await provider.complete([{"role": "user", "content": "hi"}])
