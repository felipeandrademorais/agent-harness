"""
Tests for LangGraph StateGraph agent integration in Agent Harness.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from harness.agents.graph import build_harness_graph, should_continue
from harness.agents.state import AgentState
from harness.agents.tools_adapter import build_all_langchain_tools
from harness.providers.chat_model import LiteLLMChatModel, langchain_messages_to_dict
from harness.providers.llm_provider import LLMResponse, ToolCall
from harness.skills.base import BaseSkill, SkillContext, SkillResult
from harness.skills.registry import SkillRegistry
from harness.soul.loader import Soul


class SampleSkill(BaseSkill):
    name = "sample_skill"
    description = "A sample skill for testing"

    async def execute(self, task: str, context: SkillContext) -> SkillResult:
        return SkillResult(content=f"Executed task: {task}", skill_name=self.name)


@pytest.fixture
def mock_soul() -> Soul:
    return Soul(
        name="TestBot",
        mood="professional",
        tone="Helpful",
        require_confirmation_patterns=["rm -rf*"],
        auto_approve_patterns=["ls*"],
    )


@pytest.fixture
def mock_llm_provider() -> MagicMock:
    provider = MagicMock()
    provider.complete = AsyncMock()
    return provider


def test_langchain_messages_to_dict():
    messages = [
        SystemMessage(content="System prompt"),
        HumanMessage(content="User input"),
        AIMessage(content="AI response", tool_calls=[{"id": "c1", "name": "fn1", "args": {"a": 1}}]),
        ToolMessage(content="Tool result", tool_call_id="c1"),
    ]

    converted = langchain_messages_to_dict(messages)
    assert len(converted) == 4
    assert converted[0]["role"] == "system"
    assert converted[1]["role"] == "user"
    assert converted[2]["role"] == "assistant"
    assert converted[2]["tool_calls"][0]["function"]["name"] == "fn1"
    assert converted[3]["role"] == "tool"


def test_tools_adapter_building(mock_llm_provider, mock_soul):
    registry = SkillRegistry()
    registry.register(SampleSkill())

    tools = build_all_langchain_tools(
        skills=registry,
        mcp_manager=None,
        factory=None,
        llm=mock_llm_provider,
        soul=mock_soul,
    )

    assert len(tools) == 1
    assert tools[0].name == "sample_skill"


def test_should_continue():
    # No tool calls -> END
    state1: AgentState = {"messages": [AIMessage(content="Hello")]}
    assert should_continue(state1) == "__end__"

    # Tool calls without confirmation -> tools
    state2: AgentState = {
        "messages": [AIMessage(content="", tool_calls=[{"id": "1", "name": "ls", "args": {}}])]
    }
    assert should_continue(state2) == "tools"

    # Tool calls with requires_confirmation -> sandbox_approval
    state3: AgentState = {
        "messages": [
            AIMessage(content="", tool_calls=[{"id": "1", "name": "rm", "args": {"requires_confirmation": True}}])
        ]
    }
    assert should_continue(state3) == "sandbox_approval"


@pytest.mark.asyncio
async def test_langgraph_execution_flow(mock_llm_provider, mock_soul):
    mock_llm_provider.complete.side_effect = [
        LLMResponse(
            content="Invoking skill...",
            tool_calls=[ToolCall(id="tc_1", name="sample_skill", arguments={"task": "hello"})],
        ),
        LLMResponse(content="Task complete!", tool_calls=[]),
    ]

    registry = SkillRegistry()
    registry.register(SampleSkill())

    model = LiteLLMChatModel(provider=mock_llm_provider)
    tools = build_all_langchain_tools(registry, None, None, mock_llm_provider, mock_soul)
    checkpointer = MemorySaver()

    graph = build_harness_graph(
        model=model,
        tools=tools,
        soul=mock_soul,
        checkpointer=checkpointer,
    )

    config = {"configurable": {"thread_id": "test_thread_1"}}
    res = await graph.ainvoke(
        {"messages": [HumanMessage(content="Run sample skill")], "user_id": 999},
        config=config,
    )

    assert res.get("final_response") == "Task complete!"
