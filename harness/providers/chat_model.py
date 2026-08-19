"""
LiteLLMChatModel — BaseChatModel adapter wrapping LLMProvider for LangChain/LangGraph compatibility.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field


def _convert_system_message(msg: SystemMessage) -> dict[str, Any]:
    return {"role": "system", "content": msg.content}


def _convert_human_message(msg: HumanMessage) -> dict[str, Any]:
    return {"role": "user", "content": msg.content}


def _convert_ai_message(msg: AIMessage) -> dict[str, Any]:
    item: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        item["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["args"])
                    if isinstance(tc["args"], dict)
                    else str(tc["args"]),
                },
            }
            for tc in msg.tool_calls
        ]
    return item


def _convert_tool_message(msg: ToolMessage) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": msg.tool_call_id,
        "content": str(msg.content),
    }


_MESSAGE_CONVERTERS: dict[type, Any] = {
    SystemMessage: _convert_system_message,
    HumanMessage: _convert_human_message,
    AIMessage: _convert_ai_message,
    ToolMessage: _convert_tool_message,
}


def langchain_messages_to_dict(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Convert LangChain BaseMessages to OpenAI/LiteLLM dict format."""
    result: list[dict[str, Any]] = []
    for msg in messages:
        for msg_type, converter in _MESSAGE_CONVERTERS.items():
            if isinstance(msg, msg_type):
                result.append(converter(msg))
                break
        else:
            # Fallback for unknown message types
            result.append({"role": "user", "content": str(msg.content)})
    return result


class LiteLLMChatModel(BaseChatModel):
    """
    LangChain BaseChatModel wrapper around harness.providers.llm_provider.LLMProvider.
    Allows standard LangChain / LangGraph nodes to invoke LiteLLM seamlessly.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: Any = Field(...)
    bound_tools: list[dict[str, Any]] | None = Field(default=None)

    @property
    def _llm_type(self) -> str:
        return "litellm-provider"

    def bind_tools(
        self,
        tools: list[Any],
        **kwargs: Any,
    ) -> LiteLLMChatModel:
        """Bind tools to the model."""
        formatted_tools: list[dict[str, Any]] = []
        for tool in tools:
            if hasattr(tool, "as_tool_definition"):
                formatted_tools.append(tool.as_tool_definition())
            elif isinstance(tool, dict):
                formatted_tools.append(tool)
            elif hasattr(tool, "name") and hasattr(tool, "description"):
                # LangChain BaseTool or @tool
                args_schema = getattr(tool, "args_schema", None)
                parameters = {"type": "object", "properties": {}}
                if args_schema:
                    if hasattr(args_schema, "model_json_schema"):
                        parameters = args_schema.model_json_schema()
                    elif hasattr(args_schema, "schema"):
                        parameters = args_schema.schema()
                formatted_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": parameters,
                        },
                    }
                )

        return LiteLLMChatModel(
            provider=self.provider,
            bound_tools=formatted_tools,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Synchronous generation wrapper (runs async event loop internally if called sync)."""
        import asyncio

        return asyncio.run(self._agenerate(messages, stop, run_manager, **kwargs))

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Async generation using underlying LLMProvider."""
        dict_messages = langchain_messages_to_dict(messages)
        tools = kwargs.get("tools") or self.bound_tools

        llm_response = await self.provider.complete(
            messages=dict_messages,
            tools=tools if tools else None,
        )

        tool_calls: list[dict[str, Any]] = []
        for tc in llm_response.tool_calls:
            tool_calls.append(
                {
                    "name": tc.name,
                    "args": tc.arguments,
                    "id": tc.id,
                    "type": "tool_call",
                }
            )

        ai_message = AIMessage(
            content=llm_response.content or "",
            tool_calls=tool_calls if tool_calls else [],
        )

        return ChatResult(generations=[ChatGeneration(message=ai_message)])
