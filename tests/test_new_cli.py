"""Tests for the new Typer-based CLI (ah command)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from harness.cli import app


runner = CliRunner()


# ---------------------------------------------------------------------------
# Basic CLI tests
# ---------------------------------------------------------------------------

class TestCLIBasics:
    """Test basic CLI functionality."""
    
    def test_version(self):
        """Test --version flag."""
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "Agent Harness" in result.stdout
        assert "0.1.0" in result.stdout
    
    def test_help(self):
        """Test --help flag."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.stdout
        assert "start" in result.stdout
        assert "doctor" in result.stdout
        assert "config" in result.stdout
        assert "skills" in result.stdout
    
    def test_no_args_shows_help(self):
        """Test that running with no args shows help."""
        result = runner.invoke(app, [])
        # Typer returns exit code 2 with no_args_is_help=True
        assert result.exit_code in (0, 2)
        assert "Usage:" in result.stdout


# ---------------------------------------------------------------------------
# Config command tests
# ---------------------------------------------------------------------------

class TestConfigCommands:
    """Test config subcommands."""
    
    def test_config_show_no_config(self, tmp_path, monkeypatch):
        """Test config show when no config exists."""
        monkeypatch.setenv("AGENT_HARNESS_HOME", str(tmp_path))
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 1
        assert "No configuration found" in result.stdout
    
    def test_config_show_with_config(self, tmp_path, monkeypatch):
        """Test config show with existing config."""
        monkeypatch.setenv("AGENT_HARNESS_HOME", str(tmp_path))
        
        # Create config
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "env": "dev",
            "telegram": {"token": "test123", "allowed_user_ids": []},
            "database": {"url": "postgresql://test", "pool_size": 5},
            "llm": {"provider": "ollama", "model": "test", "api_base": "http://localhost:11434", "api_key": None, "temperature": 0.7, "max_tokens": 4096},
            "context_limits": {"max_history_messages": 50, "max_context_tokens": 8192, "truncation_strategy": "sliding_window"},
            "daemon": {"heartbeat_interval": 60, "agent_timeout": 300, "telegram_notify_on_failure": True, "telegram_admin_id": None},
            "soul_file": "soul.md",
            "mcp_file": "mcp.json",
            "skills_dir": "skills",
            "logs_dir": "logs",
            "data_dir": "data",
        }))
        
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "config.json" in result.stdout
        assert "env" in result.stdout
    
    def test_config_set(self, tmp_path, monkeypatch):
        """Test config set command."""
        monkeypatch.setenv("AGENT_HARNESS_HOME", str(tmp_path))
        
        # Create initial config
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "env": "dev",
            "telegram": {"token": None, "allowed_user_ids": []},
            "database": {"url": None, "pool_size": 5},
            "llm": {"provider": "ollama", "model": "test", "api_base": "http://localhost:11434", "api_key": None, "temperature": 0.7, "max_tokens": 4096},
            "context_limits": {"max_history_messages": 50, "max_context_tokens": 8192, "truncation_strategy": "sliding_window"},
            "daemon": {"heartbeat_interval": 60, "agent_timeout": 300, "telegram_notify_on_failure": True, "telegram_admin_id": None},
            "soul_file": "soul.md",
            "mcp_file": "mcp.json",
            "skills_dir": "skills",
            "logs_dir": "logs",
            "data_dir": "data",
        }))
        
        result = runner.invoke(app, ["config", "set", "env", "prod"])
        assert result.exit_code == 0
        assert "Set env = prod" in result.stdout
        
        # Verify change
        data = json.loads(config_file.read_text())
        assert data["env"] == "prod"
    
    def test_config_path(self, tmp_path, monkeypatch):
        """Test config path command."""
        monkeypatch.setenv("AGENT_HARNESS_HOME", str(tmp_path))
        result = runner.invoke(app, ["config", "path"])
        assert result.exit_code == 0
        assert str(tmp_path) in result.stdout


# ---------------------------------------------------------------------------
# Doctor command tests
# ---------------------------------------------------------------------------

class TestDoctorCommand:
    """Test doctor command."""
    
    def test_doctor_no_config(self, tmp_path, monkeypatch):
        """Test doctor when no config exists."""
        monkeypatch.setenv("AGENT_HARNESS_HOME", str(tmp_path))
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "FAIL" in result.stdout  # At least config should fail
    
    def test_doctor_displays_table(self, tmp_path, monkeypatch):
        """Test doctor displays a status table."""
        monkeypatch.setenv("AGENT_HARNESS_HOME", str(tmp_path))
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Component" in result.stdout
        assert "Status" in result.stdout


# ---------------------------------------------------------------------------
# Status command tests
# ---------------------------------------------------------------------------

class TestStatusCommand:
    """Test status command."""
    
    def test_status_not_running(self, tmp_path, monkeypatch):
        """Test status when not running."""
        monkeypatch.setenv("AGENT_HARNESS_HOME", str(tmp_path))
        (tmp_path / "pid").mkdir(exist_ok=True)
        
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "not running" in result.stdout


# ---------------------------------------------------------------------------
# Skills command tests
# ---------------------------------------------------------------------------

class TestSkillsCommands:
    """Test skills subcommands."""
    
    def test_skills_list(self, tmp_path, monkeypatch):
        """Test skills list command."""
        monkeypatch.setenv("AGENT_HARNESS_HOME", str(tmp_path))
        (tmp_path / "skills").mkdir(exist_ok=True)
        
        result = runner.invoke(app, ["skills", "list"])
        assert result.exit_code == 0
        assert "builtin" in result.stdout or "Name" in result.stdout
    
    def test_skills_remove_builtin_fails(self, tmp_path, monkeypatch):
        """Test that removing builtin skill fails."""
        monkeypatch.setenv("AGENT_HARNESS_HOME", str(tmp_path))
        (tmp_path / "skills").mkdir(exist_ok=True)
        
        result = runner.invoke(app, ["skills", "remove", "shell"])
        assert result.exit_code == 1
        assert "builtin" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Init command tests
# ---------------------------------------------------------------------------

class TestInitCommand:
    """Test init command."""
    
    def test_init_oneshot(self, tmp_path, monkeypatch):
        """Test one-shot init with all flags."""
        monkeypatch.setenv("AGENT_HARNESS_HOME", str(tmp_path))
        
        result = runner.invoke(app, [
            "init",
            "--telegram-token=123:ABC",
            "--db-url=postgresql://test:test@localhost/test",
            "--user-ids=123,456",
            "--force",
        ])
        assert result.exit_code == 0
        assert "saved" in result.stdout.lower() or "Configuration" in result.stdout
        
        # Verify files created
        assert (tmp_path / "config.json").exists()
        assert (tmp_path / "soul.md").exists()
        assert (tmp_path / "mcp.json").exists()
    
    def test_init_creates_directories(self, tmp_path, monkeypatch):
        """Test init creates required directories."""
        monkeypatch.setenv("AGENT_HARNESS_HOME", str(tmp_path))
        
        result = runner.invoke(app, [
            "init",
            "--telegram-token=123:ABC",
            "--db-url=postgresql://test:test@localhost/test",
            "--force",
        ])
        assert result.exit_code == 0
        
        # Check directories
        assert (tmp_path / "skills").exists()
        assert (tmp_path / "logs").exists()
        assert (tmp_path / "data").exists()
        assert (tmp_path / "pid").exists()
