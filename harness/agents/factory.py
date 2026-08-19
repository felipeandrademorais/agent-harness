"""
AgentFactory — creates and manages spawned sub-agents using LangGraph.

The factory allows the PrimaryAgent to spawn sub-agents dynamically
for complex, multi-step tasks. Each spawned agent:
- Has its own goal and limited skill set
- Runs independently with its own LangGraph graph
- Reports results back to the parent
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from harness.agents.base import AgentConfig, AgentResponse
from harness.agents.graph import build_harness_graph
from harness.agents.tools_adapter import create_mcp_tool, create_skill_tool
from harness.providers.chat_model import LiteLLMChatModel

if TYPE_CHECKING:
    from harness.providers.llm_provider import LLMProvider
    from harness.providers.mcp_manager import MCPManager
    from harness.skills.registry import SkillRegistry
    from harness.soul.loader import Soul

log = structlog.get_logger(__name__)

_SPAWNED_AGENT_PROMPT_TEMPLATE = """\
Você é um sub-agente especializado com uma tarefa específica.

## Seu Objetivo
{goal}

## Skills Disponíveis
{skills_description}

## Regras
- Foque exclusivamente no objetivo definido.
- Use as skills disponíveis para completar a tarefa.
- Seja conciso nas respostas.
- Quando terminar, apresente o resultado de forma clara.
- Se não conseguir completar a tarefa, explique o motivo.

{custom_instructions}
"""


class SpawnedAgent:
    """
    A dynamically spawned sub-agent powered by LangGraph.
    """

    def __init__(
        self,
        config: AgentConfig,
        llm: LLMProvider,
        skills: SkillRegistry,
        mcp: MCPManager | None = None,
        soul: Soul | None = None,
    ) -> None:
        self.config = config
        self._llm = llm
        self._skills = skills
        self._mcp = mcp
        self._soul = soul

    async def run(self) -> AgentResponse:
        """
        Execute the agent's goal using LangGraph and return the result.
        """
        t0 = time.monotonic()
        system_prompt = self._build_system_prompt()
        chat_model = LiteLLMChatModel(provider=self._llm)

        tools = []
        for skill_name in self.config.skills:
            skill = self._skills.get(skill_name)
            if skill:
                tools.append(create_skill_tool(skill, self._llm, self._mcp, self._soul))

        if self._mcp and self._mcp.total_tools > 0:
            mcp_tools_raw = await self._mcp.list_all_tools()
            for t_def in mcp_tools_raw:
                tools.append(create_mcp_tool(t_def, self._mcp))

        graph = build_harness_graph(
            model=chat_model,
            tools=tools,
            soul=self._soul,
        )

        try:
            initial_messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Execute esta tarefa: {self.config.goal}"),
            ]
            config_run = {
                "configurable": {
                    "thread_id": f"spawned_{self.config.name}_{int(time.monotonic())}"
                }
            }

            res = await graph.ainvoke(
                {"messages": initial_messages, "user_id": 0}, config=config_run
            )

            final_content = res.get("final_response")
            if not final_content and res.get("messages"):
                final_content = (
                    getattr(res["messages"][-1], "content", "(sem resultado)")
                    or "(sem resultado)"
                )

            latency_ms = int((time.monotonic() - t0) * 1000)
            log.info(
                "spawned_agent_complete",
                agent=self.config.name,
                latency_ms=latency_ms,
            )

            return AgentResponse(
                content=final_content or "(sem resultado)",
                agent_name=self.config.name,
                success=True,
                metadata={"latency_ms": latency_ms},
            )

        except Exception as exc:
            log.error(
                "spawned_agent_error",
                agent=self.config.name,
                error=str(exc),
            )
            return AgentResponse(
                content=f"Erro ao executar: {exc}",
                agent_name=self.config.name,
                success=False,
                metadata={"error": str(exc)},
            )

    def _build_system_prompt(self) -> str:
        """Build the system prompt for this agent."""
        skill_descriptions = []
        for skill_name in self.config.skills:
            skill = self._skills.get(skill_name)
            if skill:
                skill_descriptions.append(f"- **{skill.name}**: {skill.description}")

        skills_desc = (
            "\n".join(skill_descriptions)
            if skill_descriptions
            else "(nenhuma skill específica)"
        )
        custom = self.config.system_prompt or ""

        return _SPAWNED_AGENT_PROMPT_TEMPLATE.format(
            goal=self.config.goal,
            skills_description=skills_desc,
            custom_instructions=custom,
        )


class AgentFactory:
    """
    Factory for creating and running spawned agents.
    """

    def __init__(
        self,
        llm: LLMProvider,
        skills: SkillRegistry,
        mcp: MCPManager | None = None,
        soul: Soul | None = None,
    ) -> None:
        self._llm = llm
        self._skills = skills
        self._mcp = mcp
        self._soul = soul
        self._active_agents: dict[str, SpawnedAgent] = {}

    def spawn(self, config: AgentConfig) -> SpawnedAgent:
        """Create a new spawned agent."""
        agent = SpawnedAgent(
            config=config,
            llm=self._llm,
            skills=self._skills,
            mcp=self._mcp,
            soul=self._soul,
        )
        self._active_agents[config.name] = agent
        log.info("agent_spawned", name=config.name, goal=config.goal[:50])
        return agent

    async def spawn_and_run(self, arguments: dict[str, Any]) -> str:
        """Spawn an agent and run it to completion."""
        config = AgentConfig(
            name=arguments.get("name", f"agent_{int(time.time())}"),
            goal=arguments.get("goal", ""),
            skills=arguments.get("skills", []),
            max_iterations=arguments.get("max_iterations", 10),
            system_prompt=arguments.get("system_prompt"),
        )

        if not config.goal:
            return "[Erro] O agente precisa de um objetivo (goal)."

        agent = self.spawn(config)

        try:
            result = await agent.run()
            return f"**Resultado do agente '{config.name}':**\n\n{result.content}"
        finally:
            self._active_agents.pop(config.name, None)

    def as_tool_definition(self) -> dict[str, Any]:
        """Return the spawn_agent tool definition for the PrimaryAgent."""
        available_skills = self._skills.list_names()

        return {
            "type": "function",
            "function": {
                "name": "spawn_agent",
                "description": (
                    "Cria e executa um sub-agente especializado para uma tarefa complexa. "
                    "Use quando a tarefa requer múltiplos passos ou foco específico."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Nome identificador para o agente (ex: 'research_agent')",
                        },
                        "goal": {
                            "type": "string",
                            "description": "Objetivo específico que o agente deve alcançar",
                        },
                        "skills": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": f"Skills que o agente pode usar. Disponíveis: {available_skills}",
                        },
                        "max_iterations": {
                            "type": "integer",
                            "description": "Número máximo de iterações (default: 10)",
                            "default": 10,
                        },
                    },
                    "required": ["name", "goal"],
                },
            },
        }

    def list_active(self) -> list[str]:
        """Return names of currently active agents."""
        return list(self._active_agents.keys())
