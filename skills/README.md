# User-Defined Skills

Place your custom skills in this directory. Each skill should be a Python file
that defines a class inheriting from `BaseSkill`.

## Creating a Custom Skill

1. Create a new Python file (e.g., `my_skill.py`)
2. Import the required base classes
3. Define your skill class with `name`, `description`, and `execute()`

### Example: `hello_world.py`

```python
"""Example user-defined skill."""

from harness.skills.base import BaseSkill, SkillResult, SkillContext


class HelloWorldSkill(BaseSkill):
    """A simple example skill that says hello."""

    name = "hello_world"
    description = "A friendly greeting skill that demonstrates custom skills"

    async def execute(self, task: str, context: SkillContext) -> SkillResult:
        """Execute the hello world skill."""
        name = task.strip() or "World"
        return SkillResult(
            content=f"Hello, {name}! 👋",
            skill_name=self.name,
        )
```

## Skill Structure

Every skill must define:

| Attribute | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Unique identifier for the skill |
| `description` | `str` | Human-readable description (shown in /help) |
| `execute(task, context)` | `async method` | The skill's main logic |

### SkillContext

The `context` parameter provides:

- `context.llm` — LLM provider for additional completions
- `context.mcp` — MCP manager for external tools (may be None)
- `context.history` — Conversation history
- `context.user_id` — The requesting user's ID
- `context.metadata` — Additional context from the agent

### SkillResult

Return a `SkillResult` with:

- `content` — The result text to return
- `skill_name` — Your skill's name
- `success` — Boolean (default: True)
- `requires_confirmation` — If True, agent will ask user to confirm
- `confirmation_message` — Message shown when confirmation is needed
- `metadata` — Additional data (tokens used, latency, etc.)

## Global Skills

You can also place skills in `~/.harness/skills/` to make them available
across all projects.

## Tips

1. Use `context.llm` for complex tasks that need AI reasoning
2. Use `context.mcp` to access external tools (filesystem, databases, etc.)
3. Return clear, actionable content in your `SkillResult`
4. Handle errors gracefully and return helpful error messages
