"""
BaseSkill — abstract base class for all skills.

A Skill is a reusable capability that can be invoked by the PrimaryAgent
or by spawned sub-agents. Skills differ from MCP tools in that they:

1. Have a dedicated system prompt for complex tasks
2. Can orchestrate multiple tool calls
3. May have their own agentic loop
4. Can be enabled/disabled via configuration

Usage::

    class CodeReviewSkill(BaseSkill):
        name = "code_review"
        description = "Reviews code for quality, security, and best practices"

        async def execute(self, task: str, context: SkillContext) -> SkillResult:
            # Implementation
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from harness.providers.llm_provider import LLMProvider
    from harness.providers.mcp_manager import MCPManager


@dataclass
class SkillContext:
    """
    Context passed to a skill during execution.

    Contains references to shared resources the skill might need.
    """

    llm: LLMProvider
    mcp: MCPManager | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    user_id: int | None = None
    # Additional context from the invoking agent
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillResult:
    """
    Result returned by a skill after execution.
    """

    content: str
    skill_name: str
    success: bool = True
    # Additional metadata (tokens used, latency, etc.)
    metadata: dict[str, Any] = field(default_factory=dict)
    # If the skill needs user confirmation for something
    requires_confirmation: bool = False
    confirmation_message: str | None = None


class BaseSkill(ABC):
    """
    Abstract base class for all skills.

    Subclasses must define:
    - name: Unique identifier for the skill
    - description: What the skill does (used for LLM routing)
    - system_prompt: Instructions for the skill's behavior

    And implement:
    - execute(): The main skill logic
    """

    # Class-level attributes — override in each concrete skill
    name: str = ""
    description: str = ""
    system_prompt: str = ""

    # Whether this skill requires MCP tools
    requires_mcp: bool = False

    # List of MCP tool names this skill uses (for documentation)
    mcp_tools: ClassVar[list[str]] = []

    def __init__(self) -> None:
        """Initialize the skill. Override in subclasses if needed."""

    @abstractmethod
    async def execute(
        self,
        task: str,
        context: SkillContext,
    ) -> SkillResult:
        """
        Execute the skill's main logic.

        :param task: The task description from the user/agent.
        :param context: Shared resources (LLM, MCP, history).
        :returns: SkillResult with the output.
        """

    def as_tool_definition(self) -> dict[str, Any]:
        """
        Return an OpenAI-compatible function tool definition.

        This allows the PrimaryAgent to invoke skills via tool calling.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The specific task for this skill to perform.",
                        }
                    },
                    "required": ["task"],
                },
            },
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
