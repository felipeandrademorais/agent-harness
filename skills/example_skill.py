"""
Example user-defined skill.

This file demonstrates how to create a custom skill that can be loaded
by the Agent Harness. Place your skill files in ./skills/ or ~/.harness/skills/.
"""
from harness.skills.base import BaseSkill, SkillResult, SkillContext


class HelloWorldSkill(BaseSkill):
    """A simple example skill that says hello."""
    
    name = "hello_world"
    description = "A friendly greeting skill that demonstrates custom skills"
    
    async def execute(self, task: str, context: SkillContext) -> SkillResult:
        """
        Execute the hello world skill.
        
        :param task: The task string (used as the name to greet)
        :param context: Execution context with LLM, MCP, etc.
        :returns: A friendly greeting.
        """
        name = task.strip() or "World"
        
        greeting = f"Hello, {name}! 👋"
        
        # Optionally use the LLM to generate a more creative greeting
        if context.llm and "creative" in task.lower():
            try:
                response = await context.llm.complete(
                    messages=[
                        {"role": "system", "content": "Generate a creative, unique greeting."},
                        {"role": "user", "content": f"Say hello to {name} in a creative way."},
                    ]
                )
                greeting = response.content or greeting
            except Exception:
                pass  # Fall back to simple greeting
        
        return SkillResult(
            content=greeting,
            skill_name=self.name,
        )
