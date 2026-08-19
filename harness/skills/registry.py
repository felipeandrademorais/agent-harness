"""
SkillRegistry — central catalogue of available skills.

Skills can be:
1. Built-in (from harness/skills/builtin/)
2. User-defined (from ./skills/ or ~/.harness/skills/)

The registry loads skills based on configuration and makes them
available to the PrimaryAgent for invocation.

Usage::

    registry = SkillRegistry()
    registry.load_from_config("config/skills.yaml")
    registry.load_builtin_skills()

    skill = registry.get("code_review")
    result = await skill.execute(task, context)
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import structlog
import yaml

from harness.core.exceptions import BOUNDARY_ERRORS
from harness.skills.base import BaseSkill

log = structlog.get_logger(__name__)


class SkillRegistry:
    """
    Central registry for all available skills.

    Manages skill discovery, loading, and lookup.
    """

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        """
        Register a skill instance.

        :param skill: The skill to register.
        :raises ValueError: If the skill has no name.
        """
        if not skill.name:
            raise ValueError(f"Skill {skill!r} has no name.")

        if skill.name in self._skills:
            log.warning("skill_already_registered", name=skill.name)

        self._skills[skill.name] = skill
        log.debug("skill_registered", name=skill.name)

    def unregister(self, name: str) -> bool:
        """
        Remove a skill from the registry.

        :param name: The skill name to remove.
        :returns: True if removed, False if not found.
        """
        if name in self._skills:
            del self._skills[name]
            log.debug("skill_unregistered", name=name)
            return True
        return False

    def get(self, name: str) -> BaseSkill | None:
        """Get a skill by name, or None if not found."""
        return self._skills.get(name)

    def list_all(self) -> list[BaseSkill]:
        """Return all registered skills."""
        return list(self._skills.values())

    def list_names(self) -> list[str]:
        """Return names of all registered skills."""
        return list(self._skills.keys())

    def as_tools(self) -> list[dict[str, Any]]:
        """Return all skills as OpenAI-compatible tool definitions."""
        return [s.as_tool_definition() for s in self._skills.values()]

    def load_from_config(self, config_path: str | Path) -> None:
        """
        Load skills from a YAML configuration file.

        Expected format::

            skills:
              - name: code_review
                class: harness.skills.builtin.code_review.CodeReviewSkill
                enabled: true
              - name: daily_report
                class: harness.skills.builtin.daily_report.DailyReportSkill
                enabled: true

        :param config_path: Path to the skills.yaml file.
        """
        path = Path(config_path)
        if not path.exists():
            log.warning("skills_config_not_found", path=str(path))
            return

        with path.open() as f:
            data = yaml.safe_load(f) or {}

        skill_specs: list[dict] = data.get("skills", [])
        loaded_count = 0

        for spec in skill_specs:
            skill_name = spec.get("name", "<unknown>")
            class_path = spec.get("class")
            enabled = spec.get("enabled", True)

            if not enabled:
                log.debug("skill_disabled", name=skill_name)
                continue

            if not class_path:
                log.warning("skill_missing_class", name=skill_name)
                continue

            try:
                skill = self._load_skill_class(class_path)
                self.register(skill)
                loaded_count += 1
            except BOUNDARY_ERRORS as exc:
                log.error(
                    "skill_load_failed",
                    name=skill_name,
                    class_path=class_path,
                    error=str(exc),
                )

        log.info("skills_loaded_from_config", path=str(path), count=loaded_count)

    def _load_skill_class(self, class_path: str) -> BaseSkill:
        """
        Dynamically import and instantiate a skill class.

        :param class_path: Fully qualified class path (e.g., 'harness.skills.builtin.shell.ShellSkill')
        :returns: Instantiated skill.
        """
        module_path, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        klass = getattr(module, class_name)
        return klass()

    def load_builtin_skills(self) -> None:
        """
        Load all built-in skills from harness/skills/builtin/.

        This is a convenience method that registers all skills
        without requiring them to be listed in config.
        """
        from harness.skills.builtin import get_builtin_skills

        for skill in get_builtin_skills():
            self.register(skill)

        log.info("builtin_skills_loaded", count=len(self._skills))

    def load_external_skills(self, paths: list[str | Path] | None = None) -> int:
        """
        Load user-defined skills from external directories.

        By default, searches:
        1. ./skills/ (project-local skills)
        2. ~/.harness/skills/ (global user skills)

        Each skill should be a Python file with a class that inherits
        from BaseSkill. The file can export multiple skills.

        :param paths: Optional list of custom paths to search.
        :returns: Number of skills loaded.
        """
        import sys

        if paths is None:
            paths = [
                Path("./skills"),
                Path.home() / ".harness" / "skills",
            ]

        loaded_count = 0

        for skill_dir in paths:
            skill_dir = Path(skill_dir)

            if not skill_dir.exists():
                log.debug("external_skill_dir_not_found", path=str(skill_dir))
                continue

            if not skill_dir.is_dir():
                log.warning("external_skill_path_not_dir", path=str(skill_dir))
                continue

            # Add directory to sys.path temporarily for imports
            dir_str = str(skill_dir.resolve())
            if dir_str not in sys.path:
                sys.path.insert(0, dir_str)

            # Find all Python files in the directory
            for skill_file in skill_dir.glob("*.py"):
                if skill_file.name.startswith("_"):
                    continue  # Skip __init__.py, __pycache__, etc.

                try:
                    count = self._load_skills_from_file(skill_file)
                    loaded_count += count
                except BOUNDARY_ERRORS as exc:
                    log.error(
                        "external_skill_load_failed",
                        file=str(skill_file),
                        error=str(exc),
                    )

        if loaded_count > 0:
            log.info("external_skills_loaded", count=loaded_count)

        return loaded_count

    def _load_skills_from_file(self, file_path: Path) -> int:
        """
        Load all BaseSkill subclasses from a Python file.

        :param file_path: Path to the Python file.
        :returns: Number of skills loaded from the file.
        """
        import importlib.util
        import inspect

        module_name = file_path.stem

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module from {file_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        loaded = 0

        # Find all BaseSkill subclasses in the module
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if obj is BaseSkill:
                continue  # Skip the base class itself

            if issubclass(obj, BaseSkill) and hasattr(obj, "name") and obj.name:
                try:
                    skill = obj()
                    self.register(skill)
                    loaded += 1
                    log.debug(
                        "external_skill_loaded",
                        name=skill.name,
                        file=str(file_path),
                    )
                except BOUNDARY_ERRORS as exc:
                    log.error(
                        "external_skill_instantiation_failed",
                        class_name=name,
                        file=str(file_path),
                        error=str(exc),
                    )

        return loaded

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __repr__(self) -> str:
        return f"<SkillRegistry skills={list(self._skills.keys())}>"
