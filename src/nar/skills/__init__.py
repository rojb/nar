"""Skills: folders with a ``SKILL.md`` file of specialized instructions."""

from nar.skills.loader import (
    Skill,
    SkillError,
    discover_skills,
    parse_skill,
    project_skills_dir,
    read_instructions,
    user_skills_dir,
)

__all__ = [
    "Skill",
    "SkillError",
    "discover_skills",
    "parse_skill",
    "project_skills_dir",
    "read_instructions",
    "user_skills_dir",
]
