"""
LLMProvider — unified async wrapper over LiteLLM.

Supports any LiteLLM-compatible model string. Ollama is the default local
provider; cloud providers (OpenAI, Anthropic, Gemini …) are available by
setting the appropriate API key environment variables.

Usage::

    provider = LLMProvider.from_env()
    response = await provider.complete(
        messages=[{"role": "user", "content": "Hello"}]
    )
    print(response.content)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import litellm
import structlog

log = structlog.get_logger(__name__)

# Silence LiteLLM's own verbose logging — we handle logging ourselves.
litellm.suppress_debug_info = True


@dataclass
class ToolCall:
    """Represents a single tool/function call requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Normalised response from any LiteLLM provider."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    # Raw litellm response — available for debugging
    raw: Any = field(default=None, repr=False)


class LLMProviderError(Exception):
    """Raised when the LLM provider returns an error or times out."""


class LLMProvider:
    """Async LiteLLM wrapper with Ollama-aware configuration."""

    def __init__(
        self,
        model: str,
        api_base: str | None = None,
        timeout: float = 120.0,
        **kwargs: Any,
    ) -> None:
        self.model = model
        self.api_base = api_base
        self.timeout = timeout
        self._extra: dict[str, Any] = kwargs

    @classmethod
    def from_env(cls) -> LLMProvider:
        """
        Build a provider from environment variables.

        Required:
            OLLAMA_MODEL      — LiteLLM model string, e.g. ``ollama_chat/llama3.1``
            OLLAMA_BASE_URL   — Ollama server URL, e.g. ``http://localhost:11434``

        Optional:
            LLM_TIMEOUT       — Request timeout in seconds (default: 120)
        """
        model = os.environ.get("OLLAMA_MODEL", "ollama_chat/llama3.1")
        api_base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        timeout = float(os.environ.get("LLM_TIMEOUT", "120"))
        return cls(model=model, api_base=api_base, timeout=timeout)

    def _build_kwargs(self, tools: list[dict] | None) -> dict[str, Any]:
        """Assemble the kwargs dict for litellm.acompletion."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "timeout": self.timeout,
            **self._extra,
        }

        # Inject api_base for local Ollama models
        if self.api_base and self.model.startswith(("ollama/", "ollama_chat/")):
            kwargs["api_base"] = self.api_base

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        return kwargs

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """
        Call the LLM asynchronously and return a normalised :class:`LLMResponse`.

        :param messages: OpenAI-format message list.
        :param tools: Optional OpenAI-format tool definitions.
        :raises LLMProviderError: on any provider-side error.
        """
        kwargs = self._build_kwargs(tools)
        t0 = time.monotonic()

        try:
            raw = await litellm.acompletion(messages=messages, **kwargs)
        except Exception as exc:
            raise LLMProviderError(f"LiteLLM error: {exc}") from exc

        latency_ms = int((time.monotonic() - t0) * 1000)

        choice = raw.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"raw": tc.function.arguments}
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=args,
                    )
                )

        usage = {}
        if raw.usage:
            usage = {
                "prompt_tokens": raw.usage.prompt_tokens or 0,
                "completion_tokens": raw.usage.completion_tokens or 0,
                "total_tokens": raw.usage.total_tokens or 0,
            }

        log.debug(
            "llm_complete",
            model=self.model,
            latency_ms=latency_ms,
            tool_calls=len(tool_calls),
            **usage,
        )

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            usage=usage,
            raw=raw,
        )
