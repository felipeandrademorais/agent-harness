"""
Built-in skills that come with the harness.

These skills are always available and can be enabled/disabled via config.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness.skills.builtin.ado import ADOSkill
from harness.skills.builtin.code_review import CodeReviewSkill
from harness.skills.builtin.daily_report import DailyReportSkill
from harness.skills.builtin.shell import ShellSkill
from harness.skills.builtin.sql import SQLSkill

if TYPE_CHECKING:
    from harness.skills.base import BaseSkill


def get_builtin_skills() -> list[BaseSkill]:
    """Return instances of all built-in skills."""
    return [
        ShellSkill(),
        DailyReportSkill(),
        CodeReviewSkill(),
        ADOSkill(),
        SQLSkill(),
    ]


__all__ = ["get_builtin_skills"]
