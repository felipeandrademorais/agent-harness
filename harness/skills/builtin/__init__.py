"""
Built-in skills that come with the harness.

These skills are always available and can be enabled/disabled via config.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.skills.base import BaseSkill


def get_builtin_skills() -> list[BaseSkill]:
    """
    Return instances of all built-in skills.

    Import here to avoid circular imports and allow lazy loading.
    """
    skills: list[BaseSkill] = []

    # Shell skill (requires sandbox)
    try:
        from harness.skills.builtin.shell import ShellSkill

        skills.append(ShellSkill())
    except ImportError:
        pass

    # Daily report skill
    try:
        from harness.skills.builtin.daily_report import DailyReportSkill

        skills.append(DailyReportSkill())
    except ImportError:
        pass

    # Code review skill
    try:
        from harness.skills.builtin.code_review import CodeReviewSkill

        skills.append(CodeReviewSkill())
    except ImportError:
        pass

    # ADO skill
    try:
        from harness.skills.builtin.ado import ADOSkill

        skills.append(ADOSkill())
    except ImportError:
        pass

    # SQL skill
    try:
        from harness.skills.builtin.sql import SQLSkill

        skills.append(SQLSkill())
    except ImportError:
        pass

    return skills


__all__ = ["get_builtin_skills"]
