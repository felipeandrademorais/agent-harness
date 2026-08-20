"""
graph.py — LangGraph StateGraph builder for Agent Harness.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from harness.agents.state import AgentState
from harness.providers.chat_model import LiteLLMChatModel
from harness.soul.loader import Soul

log = structlog.get_logger(__name__)

# Maximum iterations safety limit
MAX_GRAPH_STEPS = 15


def create_agent_node(model: LiteLLMChatModel, soul: Soul | None):
    """Factory creating the primary LLM agent node."""

    async def agent_node(state: AgentState) -> dict[str, Any]:
        messages = list(state.get("messages", []))

        # Ensure system prompt from Soul is present at top
        if soul and not any(isinstance(m, SystemMessage) for m in messages):
            system_prompt = soul.build_system_prompt()
            messages.insert(0, SystemMessage(content=system_prompt))

        tool_msg_count = sum(1 for m in messages if isinstance(m, ToolMessage))
        if tool_msg_count >= MAX_GRAPH_STEPS:
            log.warning("max_graph_steps_reached", count=tool_msg_count)
            summary_prompt = HumanMessage(
                content="Você atingiu o limite de iterações. Resuma o que foi feito."
            )
            response = await model.ainvoke(messages + [summary_prompt])
            return {"messages": [response], "final_response": response.content}

        # Invoke model with bound tools
        response = await model.ainvoke(messages)

        updates: dict[str, Any] = {"messages": [response]}
        if not getattr(response, "tool_calls", None):
            updates["final_response"] = response.content

        return updates

    return agent_node


def should_continue(state: AgentState) -> str:
    """Conditional edge evaluating whether to continue to tools, sandbox approval, or END."""
    messages = state.get("messages", [])
    if not messages:
        return END

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        # Check if any tool call contains a confirmation flag
        for tc in last_message.tool_calls:
            args = tc.get("args", {})
            if isinstance(args, dict) and args.get("requires_confirmation"):
                return "sandbox_approval"
        return "tools"

    return END


async def sandbox_approval_node(state: AgentState) -> dict[str, Any]:
    """Node implementing Human-in-the-Loop via LangGraph interrupt()."""
    messages = state.get("messages", [])
    last_message = messages[-1]

    pending_tc = last_message.tool_calls[0] if getattr(last_message, "tool_calls", None) else {}

    # Interrupt execution and wait for Telegram user approval
    approval_result = interrupt(
        {
            "question": f"Ação perigosa detectada em '{pending_tc.get('name')}'. Deseja autorizar?",
            "action": pending_tc,
        }
    )

    if isinstance(approval_result, dict) and approval_result.get("approved"):
        log.info("sandbox_action_approved_by_user", tool=pending_tc.get("name"))
        return {"pending_confirmation": None}
    else:
        log.warning("sandbox_action_denied_by_user", tool=pending_tc.get("name"))
        rejection_msg = ToolMessage(
            tool_call_id=pending_tc.get("id", "tc_rejected"),
            content="[Ação CANCELADA pelo usuário por motivos de segurança]",
        )
        return {"messages": [rejection_msg], "pending_confirmation": "rejected"}


def _after_sandbox_approval(state: AgentState) -> str:
    """Route after sandbox approval based on whether user approved or rejected."""
    # If rejected, the last message is a ToolMessage with rejection
    # and pending_confirmation is set to "rejected"
    if state.get("pending_confirmation") == "rejected":
        return "agent"
    # If approved, continue to tools to execute the pending tool call
    return "tools"


def build_harness_graph(
    model: LiteLLMChatModel,
    tools: Sequence[BaseTool],
    soul: Soul | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """
    Build and compile the LangGraph StateGraph for Agent Harness.
    """
    # Bind tools to model
    model_with_tools = model.bind_tools(tools)

    builder = StateGraph(AgentState)

    # Add Nodes
    builder.add_node("agent", create_agent_node(model_with_tools, soul))
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("sandbox_approval", sandbox_approval_node)

    # Set Entry Point
    builder.set_entry_point("agent")

    # Add Edges
    builder.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "sandbox_approval": "sandbox_approval",
            END: END,
        },
    )
    builder.add_edge("tools", "agent")
    # Conditional edge from sandbox_approval:
    # - If approved: go to tools
    # - If rejected: go back to agent (to process the rejection message)
    builder.add_conditional_edges(
        "sandbox_approval",
        _after_sandbox_approval,
        {
            "tools": "tools",
            "agent": "agent",
        },
    )

    # Compile graph
    saver = checkpointer or MemorySaver()
    return builder.compile(checkpointer=saver)
