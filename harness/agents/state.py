"""
AgentState — LangGraph state schema for Agent Harness.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """
    State dictionary passed through all LangGraph nodes.

    - messages: List of LangChain messages, updated with add_messages reducer.
    - user_id: ID of the Telegram user.
    - pending_confirmation: Required confirmation payload if sandbox flags an operation.
    - final_response: str | None
    """

    messages: Annotated[list[BaseMessage], add_messages]
    user_id: int
    pending_confirmation: dict[str, Any] | None
    final_response: str | None
