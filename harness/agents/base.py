"""
Agent base classes.

:class:`BaseAgent` defines the contract for spawned sub-agents.
:class:`AgentResponse` carries the agent's output.

Note: The PrimaryAgent is in primary.py and has a different interface
since it's the main orchestrator, not a spawnable sub-agent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from harness.providers.llm_provider import LLMProvider
    from harness.providers.mcp_manager import MCPManager
    from harness.skills.registry import SkillRegistry


@dataclass
class AgentResponse:
    """Response returned by an agent."""
    content: str
    agent_name: str
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentConfig:
    """Configuration for spawning an agent."""
    name: str
    goal: str
    skills: list[str] = field(default_factory=list)  # Skills this agent can use
    max_iterations: int = 10
    system_prompt: str | None = None  # Optional custom prompt


class BaseAgent(ABC):
    """
    Abstract base for spawnable sub-agents.
    
    Sub-agents are created by the AgentFactory and run to completion
    on a specific task. They have a limited scope compared to the
    PrimaryAgent.
    """
    
    name: str = ""
    goal: str = ""
    
    def __init__(
        self,
        llm_provider: "LLMProvider",
        skills: "SkillRegistry",
        mcp: "MCPManager | None" = None,
        config: AgentConfig | None = None,
    ) -> None:
        self.llm = llm_provider
        self.skills = skills
        self.mcp = mcp
        self.config = config or AgentConfig(name="unnamed", goal="")
        self.name = self.config.name
        self.goal = self.config.goal
    
    @abstractmethod
    async def run(self) -> AgentResponse:
        """
        Execute the agent's task and return the result.
        
        The agent runs until the goal is achieved or max_iterations
        is reached.
        """
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} goal={self.goal[:50]!r}>"
