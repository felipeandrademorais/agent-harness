"""
tools_adapter — Converts Harness Skills, MCP Tools, and AgentFactory into LangChain BaseTool objects.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

from harness.core.exceptions import BOUNDARY_ERRORS
from harness.providers.llm_provider import LLMProvider
from harness.providers.mcp_manager import MCPManager
from harness.skills.base import BaseSkill, SkillContext
from harness.skills.registry import SkillRegistry
from harness.soul.loader import Soul


class SkillTaskInput(BaseModel):
    task: str = Field(..., description="The specific task for this skill to perform.")


class SpawnAgentInput(BaseModel):
    goal: str = Field(..., description="The specific goal for the sub-agent to achieve.")
    skills: list[str] = Field(
        default_factory=list, description="List of skill names to give the sub-agent."
    )


def create_skill_tool(
    skill: BaseSkill,
    llm: LLMProvider,
    mcp: MCPManager | None,
    soul: Soul | None,
) -> BaseTool:
    """Wrap a Harness BaseSkill as a LangChain BaseTool."""

    async def _arun(task: str, **kwargs: Any) -> str:
        user_id = kwargs.get("user_id", 0)
        context = SkillContext(
            llm=llm,
            mcp=mcp,
            history=[],
            user_id=user_id,
            metadata={"soul": soul} if soul else {},
        )
        try:
            result = await skill.execute(task, context)
            if result.requires_confirmation:
                return f"[Aguardando confirmação]\n{result.confirmation_message}"
            return result.content
        except BOUNDARY_ERRORS as exc:
            return f"[Erro ao executar skill '{skill.name}'] {exc}"

    return StructuredTool.from_function(
        coroutine=_arun,
        name=skill.name,
        description=skill.description,
        args_schema=SkillTaskInput,
    )


def create_mcp_tool(
    tool_def: dict[str, Any],
    mcp_manager: MCPManager,
) -> BaseTool:
    """Wrap an MCP tool definition as a LangChain BaseTool."""
    func_info = tool_def.get("function") or {}
    name = func_info.get("name", "mcp_tool")
    description = func_info.get("description", "MCP Tool")
    params_schema = func_info.get("parameters") or {}
    params = params_schema.get("properties") or {}

    fields: dict[str, Any] = {}
    for p_name, p_info in params.items():
        p_type = p_info.get("type", "string")
        py_type: Any = str
        if p_type == "integer":
            py_type = int
        elif p_type == "boolean":
            py_type = bool
        elif p_type == "array":
            py_type = list
        fields[p_name] = (
            py_type,
            Field(default=None, description=p_info.get("description", "")),
        )

    schema_model = create_model(f"{name}_schema", **fields) if fields else None

    async def _arun(**kwargs: Any) -> str:
        res = await mcp_manager.call_tool(name, kwargs)
        if res.is_error:
            return f"[Erro] {res.content}"
        return res.content

    return StructuredTool.from_function(
        coroutine=_arun,
        name=name,
        description=description,
        args_schema=schema_model,
    )


def create_spawn_agent_tool(factory: Any) -> BaseTool:
    """Wrap AgentFactory.spawn_and_run as a LangChain BaseTool."""

    async def _arun(goal: str, skills: list[str] | None = None, **kwargs: Any) -> str:
        try:
            return await factory.spawn_and_run({"goal": goal, "skills": skills or []})
        except BOUNDARY_ERRORS as exc:
            return f"[Erro ao spawnar agente] {exc}"

    return StructuredTool.from_function(
        coroutine=_arun,
        name="spawn_agent",
        description="Spawn a sub-agent for complex, long-running, or multi-step tasks.",
        args_schema=SpawnAgentInput,
    )


def build_all_langchain_tools(
    skills: SkillRegistry,
    mcp_manager: MCPManager | None,
    factory: Any | None,
    llm: LLMProvider,
    soul: Soul | None = None,
) -> list[BaseTool]:
    """Build complete list of LangChain BaseTool objects for the graph."""
    tools: list[BaseTool] = []

    # 1. Add skills
    for skill in skills.list_all():
        tools.append(create_skill_tool(skill, llm, mcp_manager, soul))

    # 2. Add MCP tools
    if mcp_manager and mcp_manager.total_tools > 0:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                raw_mcp_tools = (
                    loop.run_until_complete(mcp_manager.list_all_tools())
                    if not loop.is_running()
                    else []
                )
            else:
                raw_mcp_tools = asyncio.run(mcp_manager.list_all_tools())
        except BOUNDARY_ERRORS:
            raw_mcp_tools = []
        for t_def in raw_mcp_tools:
            tools.append(create_mcp_tool(t_def, mcp_manager))

    # 3. Add spawn_agent tool
    if factory:
        tools.append(create_spawn_agent_tool(factory))

    return tools
