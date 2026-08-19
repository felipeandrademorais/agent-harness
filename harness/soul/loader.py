"""
Soul loader — loads personality configuration from YAML or Markdown files.

The Soul defines:
- Personality (mood, tone, language, values)
- Behavioral rules (what requires confirmation, what's auto-approved)
- System prompt template

Supports two formats:
1. YAML (config/soul.yaml) — legacy format
2. Markdown with YAML frontmatter (~/.agent-harness/soul.md) — new format

Usage::

    soul = load_soul("config/soul.yaml")
    soul = load_soul("~/.agent-harness/soul.md")
    system_prompt = soul.build_system_prompt()

    # Check if a command requires confirmation
    if soul.requires_confirmation("rm -rf /tmp/test"):
        # Ask user for confirmation
        ...
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import yaml

log = structlog.get_logger(__name__)


@dataclass
class Soul:
    """
    The AI's personality and behavioral configuration.

    Loaded from a YAML or Markdown file, the Soul shapes how the AI communicates
    and makes decisions about actions.
    """

    # Identity
    name: str = "Harness"
    version: str = "1.0"

    # Personality
    mood: str = "professional"
    tone: str = "Direct and helpful."
    language: str = "pt-BR"
    values: list[str] = field(default_factory=list)

    # Behavioral rules
    require_confirmation_patterns: list[str] = field(default_factory=list)
    auto_approve_patterns: list[str] = field(default_factory=list)

    # Capabilities description
    capabilities: str = ""

    # System prompt template
    system_prompt_template: str = ""

    # Raw config for extensions
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def build_system_prompt(self) -> str:
        """
        Build the complete system prompt by filling in the template.

        :returns: Formatted system prompt string.
        """
        if not self.system_prompt_template:
            return self._default_system_prompt()

        values_formatted = "\n".join(f"- {v}" for v in self.values)

        try:
            return self.system_prompt_template.format(
                name=self.name,
                mood=self.mood,
                tone=self.tone,
                language=self.language,
                values=values_formatted,
                capabilities=self.capabilities,
            )
        except KeyError as e:
            log.warning("soul_template_missing_key", key=str(e))
            return self._default_system_prompt()

    def _default_system_prompt(self) -> str:
        """Fallback system prompt if no template is defined."""
        values_formatted = "\n".join(f"- {v}" for v in self.values)
        return f"""Você é {self.name}, um assistente de IA.

Mood: {self.mood}
{self.tone}

Responda em {self.language}.

Valores:
{values_formatted}

{self.capabilities}
"""

    def requires_confirmation(self, command: str) -> bool:
        """
        Check if a command requires user confirmation before execution.

        :param command: The shell command to check.
        :returns: True if confirmation is required, False otherwise.
        """
        command = command.strip()

        # First check if it matches any dangerous pattern
        for pattern in self.require_confirmation_patterns:
            if self._matches_pattern(command, pattern):
                log.debug(
                    "command_requires_confirmation", command=command, pattern=pattern
                )
                return True

        return False

    def is_auto_approved(self, command: str) -> bool:
        """
        Check if a command is in the auto-approve list (safe to execute).

        :param command: The shell command to check.
        :returns: True if auto-approved, False otherwise.
        """
        command = command.strip()

        for pattern in self.auto_approve_patterns:
            if self._matches_pattern(command, pattern):
                log.debug("command_auto_approved", command=command, pattern=pattern)
                return True

        return False

    def get_permission_status(self, command: str) -> str:
        """
        Get the permission status for a command.

        :param command: The shell command to check.
        :returns: "denied" if requires confirmation, "approved" if auto-approved,
                  "unknown" if neither (will prompt user).
        """
        if self.requires_confirmation(command):
            return "denied"
        if self.is_auto_approved(command):
            return "approved"
        return "unknown"

    @staticmethod
    def _matches_pattern(command: str, pattern: str) -> bool:
        """
        Check if a command matches a pattern.

        Supports:
        - Wildcard patterns with * (fnmatch style)
        - Exact prefix matching
        """
        # Normalize
        command = command.lower().strip()
        pattern = pattern.lower().strip()

        # Try fnmatch first (handles wildcards)
        if fnmatch.fnmatch(command, pattern):
            return True

        # Also check if command starts with pattern (minus trailing *)
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            if command.startswith(prefix):
                return True

        # Exact match
        return command == pattern


def _parse_markdown_soul(content: str) -> dict[str, Any]:
    """
    Parse a Markdown soul file with YAML frontmatter.

    Expected format:
    ---
    name: Harness
    mood: professional
    language: pt-BR
    values:
      - Value 1
      - Value 2
    behaviors:
      require_confirmation:
        - "rm -rf *"
      auto_approve:
        - "ls *"
    ---

    (Markdown body becomes the system_prompt_template)

    :param content: Raw file content.
    :returns: Parsed configuration dict.
    :raises ValueError: If frontmatter is invalid.
    """
    if not content.startswith("---"):
        raise ValueError("Markdown soul file must start with YAML frontmatter (---)")

    # Split frontmatter and body
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError(
            "Invalid frontmatter format. Expected: ---\\nYAML\\n---\\nBody"
        )

    frontmatter_yaml = parts[1].strip()
    body = parts[2].strip()

    data = yaml.safe_load(frontmatter_yaml) or {}

    # Body becomes the system prompt template
    if body:
        data["system_prompt_template"] = body

    return data


def _load_soul_from_yaml(path: Path) -> Soul:
    """Load Soul from a YAML file."""
    with path.open() as f:
        data = yaml.safe_load(f) or {}

    personality = data.get("personality", {})

    behaviors = data.get("behaviors", {})

    capabilities_data = data.get("capabilities", {})

    return Soul(
        name=data.get("name", "Harness"),
        version=data.get("version", "1.0"),
        mood=personality.get("mood", "professional"),
        tone=personality.get("tone", "Direct and helpful."),
        language=personality.get("language", "pt-BR"),
        values=personality.get("values", []),
        require_confirmation_patterns=behaviors.get("require_confirmation", []),
        auto_approve_patterns=behaviors.get("auto_approve", []),
        capabilities=capabilities_data.get("description", ""),
        system_prompt_template=data.get("system_prompt_template", ""),
        _raw=data,
    )


def _load_soul_from_markdown(path: Path) -> Soul:
    """Load Soul from a Markdown file with YAML frontmatter."""
    content = path.read_text()
    data = _parse_markdown_soul(content)

    # Extract behaviors (may be nested or flat)
    behaviors = data.get("behaviors", {})

    return Soul(
        name=data.get("name", "Harness"),
        version=data.get("version", "1.0"),
        mood=data.get("mood", "professional"),
        tone=data.get("tone", "Direct and helpful."),
        language=data.get("language", "pt-BR"),
        values=data.get("values", []),
        require_confirmation_patterns=behaviors.get("require_confirmation", []),
        auto_approve_patterns=behaviors.get("auto_approve", []),
        capabilities=data.get("capabilities", ""),
        system_prompt_template=data.get("system_prompt_template", ""),
        _raw=data,
    )


def load_soul(path: str | Path) -> Soul:
    """
    Load a Soul configuration from a YAML or Markdown file.

    Automatically detects format based on file extension:
    - .yaml/.yml: Legacy YAML format
    - .md: Markdown with YAML frontmatter (new format)

    :param path: Path to the soul configuration file.
    :returns: Configured Soul instance.
    :raises FileNotFoundError: If the file doesn't exist.
    """
    path = Path(path)

    if not path.exists():
        log.warning("soul_config_not_found", path=str(path))
        return Soul()  # Return default soul

    # Detect format
    suffix = path.suffix.lower()

    try:
        if suffix == ".md":
            soul = _load_soul_from_markdown(path)
        elif suffix in (".yaml", ".yml"):
            soul = _load_soul_from_yaml(path)
        else:
            # Try to detect from content
            content = path.read_text()
            if content.startswith("---"):
                soul = _load_soul_from_markdown(path)
            else:
                soul = _load_soul_from_yaml(path)
    except Exception as e:
        log.error("soul_load_failed", path=str(path), error=str(e))
        return Soul()  # Return default soul

    log.info(
        "soul_loaded",
        name=soul.name,
        mood=soul.mood,
        language=soul.language,
        format=suffix,
        dangerous_patterns=len(soul.require_confirmation_patterns),
        safe_patterns=len(soul.auto_approve_patterns),
    )

    return soul
