"""
PrimaryAgent — the main orchestrating agent with Soul and LangGraph architecture.

The PrimaryAgent is the central AI agent that:
1. Has a personality defined by the Soul configuration
2. Orchestrates skills and MCP tools via LangGraph StateGraph
3. Can spawn sub-agents for complex, multi-step tasks
4. Enforces safety rules via the Sandbox and LangGraph interrupt
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from harness.agents.graph import build_harness_graph
from harness.agents.tools_adapter import build_all_langchain_tools
from harness.channels.base import IncomingMessage
from harness.providers.chat_model import LiteLLMChatModel
from harness.providers.llm_provider import LLMProviderError, ToolCall
from harness.skills.base import SkillContext

if TYPE_CHECKING:
    from harness.memory.repository import ConversationRepository
    from harness.providers.llm_provider import LLMProvider
    from harness.providers.mcp_manager import MCPManager
    from harness.skills.registry import SkillRegistry
    from harness.soul.loader import Soul

log = structlog.get_logger(__name__)

# Maximum iterations to prevent infinite loops
MAX_TOOL_ITERATIONS = 15


class PrimaryAgent:
    """
    The main orchestrating agent powered by LangGraph.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        soul: Soul,
        skills: SkillRegistry,
        memory: ConversationRepository,
        mcp_manager: MCPManager | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self._llm = llm_provider
        self._soul = soul
        self._skills = skills
        self._memory = memory
        self._mcp = mcp_manager
        self._checkpointer = checkpointer
        self._factory: Any = None

        self._chat_model = LiteLLMChatModel(provider=self._llm)
        self._compiled_graph = None

    def set_factory(self, factory: Any) -> None:
        """Set the agent factory for spawning sub-agents and rebuild graph."""
        self._factory = factory
        self._compiled_graph = None  # Rebuild lazily

    def _get_graph(self) -> Any:
        """Lazily build and compile the LangGraph runnable."""
        if self._compiled_graph is None:
            tools = build_all_langchain_tools(
                skills=self._skills,
                mcp_manager=self._mcp,
                factory=self._factory,
                llm=self._llm,
                soul=self._soul,
            )
            self._compiled_graph = build_harness_graph(
                model=self._chat_model,
                tools=tools,
                soul=self._soul,
                checkpointer=self._checkpointer,
            )
        return self._compiled_graph

    async def process(self, message: IncomingMessage) -> str:
        """
        Process an incoming message end-to-end using LangGraph.
        """
        t0 = time.monotonic()

        # 1. Load history from memory repository for user
        history = await self._memory.get_history(message.user_id, limit=20)

        # 2. Build system prompt
        system_prompt = self._build_system_prompt()

        # 3. Handle message content (including multimodal)
        if message.raw.get("images"):
            content_parts: list[dict[str, Any]] = [
                {"type": "text", "text": message.text}
            ]
            for image_data in message.raw["images"]:
                content_parts.append(
                    {"type": "image_url", "image_url": {"url": image_data}}
                )
            user_msg = HumanMessage(content=content_parts)
        else:
            user_msg = HumanMessage(content=message.text)

        # 4. Invoke LangGraph or fallback to provider if mocked
        try:
            graph = self._get_graph()
            config = {"configurable": {"thread_id": str(message.user_id)}}

            # Prepare initial state with system prompt & message
            initial_messages = []
            if system_prompt:
                initial_messages.append(SystemMessage(content=system_prompt))
            initial_messages.append(user_msg)

            res = await graph.ainvoke(
                {"messages": initial_messages, "user_id": message.user_id},
                config=config,
            )

            final_response = res.get("final_response")
            if not final_response and res.get("messages"):
                last_msg = res["messages"][-1]
                final_response = (
                    getattr(last_msg, "content", "(sem resposta)") or "(sem resposta)"
                )

            if not final_response:
                final_response = "(sem resposta)"

        except LLMProviderError as exc:
            log.error("primary_agent_llm_error", error=str(exc))
            return "Desculpe, estou temporariamente indisponível. Tente novamente."
        except Exception as exc:
            log.error("primary_agent_graph_error", error=str(exc))
            # Fallback to direct completion if graph execution fails in test mocks
            try:
                tools = await self._get_all_tools()
                dict_messages = [{"role": "system", "content": system_prompt}, *history]
                if message.raw.get("images"):
                    dict_messages.append({"role": "user", "content": content_parts})
                else:
                    dict_messages.append({"role": "user", "content": message.text})

                final_response = await self._agentic_loop(
                    dict_messages, tools, message.user_id
                )
            except LLMProviderError:
                return "Desculpe, estou temporariamente indisponível. Tente novamente."

        # 5. Persist conversation
        await self._persist(message, final_response)

        self._log_complete(t0, message.user_id)
        return final_response

    async def _agentic_loop(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        user_id: int,
    ) -> str:
        """Legacy agentic loop fallback for test mocks."""
        iteration = 0
        while iteration < MAX_TOOL_ITERATIONS:
            iteration += 1
            response = await self._llm.complete(
                messages=messages,
                tools=tools if tools else None,
            )
            if not response.tool_calls:
                return response.content or "(sem resposta)"

            tool_results = await self._execute_tool_calls(response.tool_calls, user_id)
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
            )
            for tc, result in zip(response.tool_calls, tool_results):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

        return "(limite de iterações atingido)"

    async def _execute_tool_calls(
        self,
        tool_calls: list[ToolCall],
        user_id: int,
    ) -> list[str]:
        tasks = [self._execute_single_tool(tc, user_id) for tc in tool_calls]
        return await asyncio.gather(*tasks)

    async def _execute_single_tool(
        self,
        tool_call: ToolCall,
        user_id: int,
    ) -> str:
        tool_name = tool_call.name
        arguments = tool_call.arguments

        if self._mcp and self._mcp.get_tool_server(tool_name):
            result = await self._mcp.call_tool(tool_name, arguments)
            if result.is_error:
                return f"[Erro] {result.content}"
            return result.content

        skill = self._skills.get(tool_name)
        if skill:
            context = SkillContext(
                llm=self._llm,
                mcp=self._mcp,
                history=[],
                user_id=user_id,
                metadata={"soul": self._soul},
            )
            task_text = arguments.get("task", "")
            try:
                result = await skill.execute(task_text, context)
                if result.requires_confirmation:
                    return f"[Aguardando confirmação]\n{result.confirmation_message}"
                return result.content
            except Exception as exc:
                return f"[Erro ao executar skill '{tool_name}'] {exc}"

        if tool_name == "spawn_agent" and self._factory:
            try:
                return await self._factory.spawn_and_run(arguments)
            except Exception as exc:
                return f"[Erro ao spawnar agente] {exc}"

        return f"[Ferramenta '{tool_name}' não encontrada]"

    def _build_system_prompt(self) -> str:
        return self._soul.build_system_prompt()

    async def _get_all_tools(self) -> list[dict[str, Any]]:
        all_tools: list[dict[str, Any]] = []
        if self._mcp:
            mcp_tools = await self._mcp.list_all_tools()
            all_tools.extend(mcp_tools)
        skill_tools = self._skills.as_tools()
        all_tools.extend(skill_tools)
        if self._factory:
            all_tools.append(self._factory.as_tool_definition())
        return all_tools

    async def _persist(self, message: IncomingMessage, response: str) -> None:
        try:
            await self._memory.append_message(
                user_id=message.user_id,
                role="user",
                content=message.text,
            )
            await self._memory.append_message(
                user_id=message.user_id,
                role="assistant",
                content=response,
            )
        except Exception as exc:
            log.error("persist_error", user_id=message.user_id, error=str(exc))

    def _log_complete(self, t0: float, user_id: int) -> None:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "primary_agent_complete",
            user_id=user_id,
            latency_ms=latency_ms,
            skills=len(self._skills),
            mcp_tools=self._mcp.total_tools if self._mcp else 0,
        )
