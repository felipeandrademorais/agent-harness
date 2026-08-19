"""Tests for the Soul system (personality and behaviors)."""
import pytest
import tempfile
from pathlib import Path

from harness.soul.loader import Soul, load_soul


class TestSoulLoading:
    """Tests for loading Soul from YAML."""
    
    def test_load_soul_from_file(self, tmp_path: Path) -> None:
        """Load soul from a valid YAML file."""
        soul_yaml = tmp_path / "soul.yaml"
        soul_yaml.write_text("""
name: TestBot
version: "1.0"

personality:
  mood: playful
  tone: You speak in haiku.
  language: en-US
  values:
    - brevity
    - creativity

behaviors:
  require_confirmation:
    - rm -rf*
    - DROP TABLE*
  auto_approve:
    - ls*
    - cat*
""")
        soul = load_soul(str(soul_yaml))
        
        assert soul.name == "TestBot"
        assert soul.mood == "playful"
        assert "rm -rf*" in soul.require_confirmation_patterns
        assert "ls*" in soul.auto_approve_patterns
    
    def test_load_soul_missing_file_uses_default(self) -> None:
        """Missing file should return default soul."""
        soul = load_soul("/nonexistent/path/soul.yaml")
        
        assert soul.name == "Harness"  # Default name
        assert isinstance(soul.require_confirmation_patterns, list)
    
    def test_load_soul_empty_file(self, tmp_path: Path) -> None:
        """Empty YAML file should still return a valid soul."""
        soul_yaml = tmp_path / "empty.yaml"
        soul_yaml.write_text("")
        
        soul = load_soul(str(soul_yaml))
        
        # Should use defaults
        assert isinstance(soul.name, str)
        assert isinstance(soul.require_confirmation_patterns, list)


class TestSoulPermissions:
    """Tests for permission checking."""
    
    @pytest.fixture
    def soul(self) -> Soul:
        """Create a soul with known behaviors."""
        return Soul(
            name="Test",
            require_confirmation_patterns=["rm -rf*", "DROP TABLE*", "git push --force*"],
            auto_approve_patterns=["ls*", "cat*", "git status*", "pwd*"],
        )
    
    def test_requires_confirmation_with_wildcard(self, soul: Soul) -> None:
        """Commands matching require_confirmation patterns need confirmation."""
        assert soul.requires_confirmation("rm -rf /tmp/test") is True
        assert soul.requires_confirmation("DROP TABLE users") is True
        assert soul.requires_confirmation("git push --force origin main") is True
    
    def test_auto_approved_with_wildcard(self, soul: Soul) -> None:
        """Commands matching auto_approve patterns are auto-approved."""
        assert soul.is_auto_approved("ls -la") is True
        assert soul.is_auto_approved("cat file.txt") is True
        assert soul.is_auto_approved("git status") is True
    
    def test_unknown_command_not_auto_approved(self, soul: Soul) -> None:
        """Commands not in any list are not auto-approved."""
        assert soul.is_auto_approved("curl https://example.com") is False
        assert soul.is_auto_approved("docker run") is False
    
    def test_unknown_command_does_not_require_confirmation(self, soul: Soul) -> None:
        """Commands not in require_confirmation don't need it."""
        assert soul.requires_confirmation("echo hello") is False
    
    def test_case_insensitivity(self, soul: Soul) -> None:
        """Pattern matching should be case-insensitive."""
        # Both lowercase and uppercase should match
        assert soul.requires_confirmation("rm -rf /tmp") is True
        assert soul.requires_confirmation("RM -RF /tmp") is True


class TestSoulSystemPrompt:
    """Tests for system prompt generation."""
    
    def test_build_system_prompt_default(self) -> None:
        """System prompt uses default when no template."""
        soul = Soul(
            name="TestBot",
            mood="helpful",
            tone="Friendly and informative.",
            language="en-US",
            values=["accuracy", "clarity"],
        )
        
        prompt = soul.build_system_prompt()
        
        assert "TestBot" in prompt
        assert "helpful" in prompt
        assert "accuracy" in prompt
    
    def test_build_system_prompt_with_template(self) -> None:
        """System prompt uses the template correctly."""
        soul = Soul(
            name="CustomBot",
            mood="casual",
            tone="Laid back",
            system_prompt_template="Name: {name}\nMood: {mood}",
        )
        
        prompt = soul.build_system_prompt()
        
        assert "Name: CustomBot" in prompt
        assert "Mood: casual" in prompt
    
    def test_build_system_prompt_with_capabilities(self) -> None:
        """System prompt includes capabilities."""
        soul = Soul(
            name="ToolBot",
            capabilities="Can read files and execute shell commands.",
            system_prompt_template="{name} - {capabilities}",
        )
        
        prompt = soul.build_system_prompt()
        
        assert "ToolBot" in prompt
        assert "read files" in prompt


class TestSoulPatternMatching:
    """Tests for the pattern matching logic."""
    
    def test_exact_match(self) -> None:
        """Exact command matches pattern."""
        assert Soul._matches_pattern("ls", "ls") is True
        assert Soul._matches_pattern("pwd", "pwd") is True
    
    def test_wildcard_match(self) -> None:
        """Wildcard patterns match commands."""
        assert Soul._matches_pattern("ls -la", "ls*") is True
        assert Soul._matches_pattern("rm -rf /tmp", "rm -rf*") is True
    
    def test_no_match(self) -> None:
        """Non-matching commands return False."""
        assert Soul._matches_pattern("cat file.txt", "ls*") is False
        assert Soul._matches_pattern("echo hello", "rm*") is False
    
    def test_case_insensitive_match(self) -> None:
        """Matching is case-insensitive."""
        assert Soul._matches_pattern("LS -LA", "ls*") is True
        assert Soul._matches_pattern("Cat File.txt", "cat*") is True


class TestSoulPermissionStatus:
    """Tests for get_permission_status method."""
    
    @pytest.fixture
    def soul(self) -> Soul:
        """Create a soul for testing."""
        return Soul(
            require_confirmation_patterns=["rm*", "delete*"],
            auto_approve_patterns=["ls*", "cat*"],
        )
    
    def test_denied_status(self, soul: Soul) -> None:
        """Commands requiring confirmation return 'denied'."""
        assert soul.get_permission_status("rm -rf /tmp") == "denied"
        assert soul.get_permission_status("delete file") == "denied"
    
    def test_approved_status(self, soul: Soul) -> None:
        """Auto-approved commands return 'approved'."""
        assert soul.get_permission_status("ls -la") == "approved"
        assert soul.get_permission_status("cat file") == "approved"
    
    def test_unknown_status(self, soul: Soul) -> None:
        """Unknown commands return 'unknown'."""
        assert soul.get_permission_status("curl http://example.com") == "unknown"
        assert soul.get_permission_status("docker ps") == "unknown"


class TestSoulDefaults:
    """Tests for default soul values."""
    
    def test_default_soul_has_sensible_values(self) -> None:
        """Default soul should have reasonable defaults."""
        soul = Soul()
        
        assert soul.name == "Harness"
        assert soul.version == "1.0"
        assert soul.mood == "professional"
        assert soul.language == "pt-BR"
        assert isinstance(soul.values, list)
        assert isinstance(soul.require_confirmation_patterns, list)
        assert isinstance(soul.auto_approve_patterns, list)
