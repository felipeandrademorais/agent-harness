"""Tests for the Skill system (registry and base classes)."""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from harness.skills.base import BaseSkill, SkillResult, SkillContext
from harness.skills.registry import SkillRegistry


class TestSkillResult:
    """Tests for SkillResult dataclass."""
    
    def test_skill_result_success(self) -> None:
        """Successful skill result."""
        result = SkillResult(
            content="Task completed successfully",
            skill_name="test_skill",
        )
        
        assert result.content == "Task completed successfully"
        assert result.skill_name == "test_skill"
        assert result.success is True
        assert result.requires_confirmation is False
        assert result.confirmation_message is None
    
    def test_skill_result_failure(self) -> None:
        """Failed skill result."""
        result = SkillResult(
            content="Error occurred",
            skill_name="test_skill",
            success=False,
        )
        
        assert result.success is False
        assert result.content == "Error occurred"
    
    def test_skill_result_requires_confirmation(self) -> None:
        """Skill result requiring user confirmation."""
        result = SkillResult(
            content="",
            skill_name="dangerous_skill",
            requires_confirmation=True,
            confirmation_message="Do you want to delete 100 files?",
        )
        
        assert result.requires_confirmation is True
        assert result.confirmation_message == "Do you want to delete 100 files?"
    
    def test_skill_result_with_metadata(self) -> None:
        """Skill result with metadata."""
        result = SkillResult(
            content="Done",
            skill_name="test",
            metadata={"tokens_used": 150, "latency_ms": 500},
        )
        
        assert result.metadata["tokens_used"] == 150
        assert result.metadata["latency_ms"] == 500


class TestSkillContext:
    """Tests for SkillContext."""
    
    def test_skill_context_creation(self) -> None:
        """Create a skill context with all fields."""
        mock_llm = MagicMock()
        mock_mcp = MagicMock()
        
        context = SkillContext(
            llm=mock_llm,
            mcp=mock_mcp,
            history=[{"role": "user", "content": "hello"}],
            user_id=12345,
            metadata={"key": "value"},
        )
        
        assert context.llm is mock_llm
        assert context.mcp is mock_mcp
        assert len(context.history) == 1
        assert context.user_id == 12345
        assert context.metadata["key"] == "value"
    
    def test_skill_context_optional_mcp(self) -> None:
        """MCP manager is optional."""
        context = SkillContext(
            llm=MagicMock(),
            mcp=None,
            history=[],
            user_id=1,
        )
        
        assert context.mcp is None


class TestBaseSkill:
    """Tests for BaseSkill abstract class."""
    
    def test_cannot_instantiate_base_skill(self) -> None:
        """BaseSkill is abstract and cannot be instantiated."""
        with pytest.raises(TypeError):
            BaseSkill()
    
    def test_concrete_skill_implementation(self) -> None:
        """A concrete skill implementation works."""
        class TestSkill(BaseSkill):
            name = "test_skill"
            description = "A test skill"
            
            async def execute(self, task: str, context: SkillContext) -> SkillResult:
                return SkillResult(content=f"Executed: {task}", skill_name=self.name)
        
        skill = TestSkill()
        
        assert skill.name == "test_skill"
        assert skill.description == "A test skill"
    
    @pytest.mark.asyncio
    async def test_skill_execute(self) -> None:
        """Execute a concrete skill."""
        class EchoSkill(BaseSkill):
            name = "echo"
            description = "Echoes the task"
            
            async def execute(self, task: str, context: SkillContext) -> SkillResult:
                return SkillResult(content=f"Echo: {task}", skill_name=self.name)
        
        skill = EchoSkill()
        context = SkillContext(
            llm=MagicMock(),
            mcp=None,
            history=[],
            user_id=1,
        )
        
        result = await skill.execute("hello world", context)
        
        assert result.content == "Echo: hello world"
        assert result.success is True
        assert result.skill_name == "echo"


class TestSkillRegistry:
    """Tests for SkillRegistry."""
    
    def test_empty_registry(self) -> None:
        """Empty registry has no skills."""
        registry = SkillRegistry()
        
        assert len(registry) == 0
        assert registry.list_all() == []
    
    def test_register_skill(self) -> None:
        """Register a skill manually."""
        class CustomSkill(BaseSkill):
            name = "custom"
            description = "Custom skill"
            
            async def execute(self, task: str, context: SkillContext) -> SkillResult:
                return SkillResult(content="done", skill_name=self.name)
        
        registry = SkillRegistry()
        skill = CustomSkill()
        registry.register(skill)
        
        assert len(registry) == 1
        assert registry.get("custom") is skill
    
    def test_get_nonexistent_skill(self) -> None:
        """Getting a nonexistent skill returns None."""
        registry = SkillRegistry()
        
        assert registry.get("nonexistent") is None
    
    def test_list_all_skills(self) -> None:
        """List all registered skills."""
        class SkillA(BaseSkill):
            name = "skill_a"
            description = "Skill A"
            async def execute(self, task: str, context: SkillContext) -> SkillResult:
                return SkillResult(content="a", skill_name=self.name)
        
        class SkillB(BaseSkill):
            name = "skill_b"
            description = "Skill B"
            async def execute(self, task: str, context: SkillContext) -> SkillResult:
                return SkillResult(content="b", skill_name=self.name)
        
        registry = SkillRegistry()
        registry.register(SkillA())
        registry.register(SkillB())
        
        skills = registry.list_all()
        
        assert len(skills) == 2
        names = [s.name for s in skills]
        assert "skill_a" in names
        assert "skill_b" in names
    
    def test_load_builtin_skills(self) -> None:
        """Load builtin skills."""
        registry = SkillRegistry()
        registry.load_builtin_skills()
        
        # Should have loaded the builtin skills
        assert len(registry) > 0
        
        # Shell skill should be present
        shell = registry.get("shell")
        assert shell is not None
        assert shell.name == "shell"
    
    def test_load_from_config_with_builtins(self) -> None:
        """Load skills from config file after loading builtins."""
        registry = SkillRegistry()
        registry.load_from_config("config/skills.yaml")
        
        # After load_from_config, builtins should be loaded and enabled
        # Check that shell skill exists (it's enabled in the config)
        shell = registry.get("shell")
        assert shell is not None, "Shell skill should be loaded"
    
    def test_as_tools_format(self) -> None:
        """Convert skills to OpenAI tool format."""
        class TestSkill(BaseSkill):
            name = "test_tool"
            description = "A test tool for testing"
            
            async def execute(self, task: str, context: SkillContext) -> SkillResult:
                return SkillResult(content="done", skill_name=self.name)
        
        registry = SkillRegistry()
        registry.register(TestSkill())
        
        tools = registry.as_tools()
        
        assert len(tools) == 1
        tool = tools[0]
        
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "test_tool"
        assert "test tool for testing" in tool["function"]["description"]
        assert "parameters" in tool["function"]


class TestBuiltinSkills:
    """Tests for builtin skills loading."""
    
    def test_all_builtin_skills_have_required_attributes(self) -> None:
        """All builtin skills have name and description."""
        registry = SkillRegistry()
        registry.load_builtin_skills()
        
        for skill in registry.list_all():
            assert hasattr(skill, "name")
            assert hasattr(skill, "description")
            assert skill.name != ""
            assert skill.description != ""
    
    def test_shell_skill_exists(self) -> None:
        """Shell skill is available."""
        registry = SkillRegistry()
        registry.load_builtin_skills()
        
        shell = registry.get("shell")
        
        assert shell is not None
        assert shell.name == "shell"
    
    def test_daily_report_skill_exists(self) -> None:
        """Daily report skill is available."""
        registry = SkillRegistry()
        registry.load_builtin_skills()
        
        skill = registry.get("daily_report")
        
        assert skill is not None
        assert skill.name == "daily_report"


class TestExternalSkills:
    """Tests for external (user-defined) skills loading."""
    
    def test_load_external_skills_from_custom_path(self, tmp_path: Path) -> None:
        """Load external skills from a custom directory."""
        # Create a test skill file
        skill_file = tmp_path / "test_external.py"
        skill_file.write_text('''
from harness.skills.base import BaseSkill, SkillResult, SkillContext

class TestExternalSkill(BaseSkill):
    name = "test_external"
    description = "A test external skill"
    
    async def execute(self, task: str, context: SkillContext) -> SkillResult:
        return SkillResult(content=f"External: {task}", skill_name=self.name)
''')
        
        registry = SkillRegistry()
        count = registry.load_external_skills([tmp_path])
        
        assert count == 1
        skill = registry.get("test_external")
        assert skill is not None
        assert skill.name == "test_external"
    
    def test_load_external_skills_ignores_underscore_files(self, tmp_path: Path) -> None:
        """Files starting with _ are ignored."""
        # Create a file that should be ignored
        ignored_file = tmp_path / "_private.py"
        ignored_file.write_text('''
from harness.skills.base import BaseSkill, SkillResult, SkillContext

class IgnoredSkill(BaseSkill):
    name = "ignored"
    description = "Should not be loaded"
    
    async def execute(self, task: str, context: SkillContext) -> SkillResult:
        return SkillResult(content="", skill_name=self.name)
''')
        
        registry = SkillRegistry()
        count = registry.load_external_skills([tmp_path])
        
        assert count == 0
        assert registry.get("ignored") is None
    
    def test_load_external_skills_handles_missing_dir(self) -> None:
        """Non-existent directories are skipped gracefully."""
        registry = SkillRegistry()
        count = registry.load_external_skills([Path("/nonexistent/path")])
        
        assert count == 0
    
    def test_load_multiple_skills_from_one_file(self, tmp_path: Path) -> None:
        """Multiple skill classes in one file are all loaded."""
        skill_file = tmp_path / "multi_skills.py"
        skill_file.write_text('''
from harness.skills.base import BaseSkill, SkillResult, SkillContext

class SkillOne(BaseSkill):
    name = "skill_one"
    description = "First skill"
    
    async def execute(self, task: str, context: SkillContext) -> SkillResult:
        return SkillResult(content="one", skill_name=self.name)

class SkillTwo(BaseSkill):
    name = "skill_two"
    description = "Second skill"
    
    async def execute(self, task: str, context: SkillContext) -> SkillResult:
        return SkillResult(content="two", skill_name=self.name)
''')
        
        registry = SkillRegistry()
        count = registry.load_external_skills([tmp_path])
        
        assert count == 2
        assert registry.get("skill_one") is not None
        assert registry.get("skill_two") is not None
