"""The agent's system prompt."""

from __future__ import annotations

import platform
from datetime import date
from pathlib import Path

from nar.context.instructions import ProjectInstructions

IDENTITY = """\
You are nar, a coding agent running in the user's terminal. You help with software \
engineering tasks in the user's project: understanding code, answering questions about it, \
and making changes when asked."""

GUIDELINES = """\
# How to work
- Use your tools to look at the project. Read a file before answering questions about it \
or describing what it contains; never guess or invent file contents or tool results.
- Prefer small, focused tool calls (for example, read the relevant part of a file instead \
of everything).
- When a tool returns an error, read the message, fix the call and try again, or explain \
what went wrong.
- Refer to files with paths relative to the working directory.
- Keep answers concise and direct. When you have what you need, answer without calling \
more tools."""


def build_system_prompt(
    cwd: Path,
    instructions: ProjectInstructions | None = None,
    *,
    platform_name: str | None = None,
    today: date | None = None,
) -> str:
    """Identity, guidelines, environment and, when present, the project's ``AGENTS.md``."""
    system = platform_name or f"{platform.system()} {platform.release()}".strip()
    environment = (
        "# Environment\n"
        f"- Working directory: {cwd}\n"
        f"- Operating system: {system}\n"
        f"- Date: {(today or date.today()).isoformat()}"
    )
    sections = [IDENTITY, GUIDELINES, environment]
    if instructions is not None:
        sections.append(
            f"# Project instructions (from {instructions.path})\n"
            "Follow these instructions from the project's AGENTS.md:\n\n"
            f"{instructions.content.strip()}"
        )
    return "\n\n".join(sections)
