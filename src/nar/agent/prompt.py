"""The agent's system prompt."""

from __future__ import annotations

import platform
from collections.abc import Mapping
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
- To find files or code, search with glob (file names) and grep (file contents) before \
reading files.
- Prefer small, focused tool calls (for example, read the relevant part of a file instead \
of everything).
- When a tool returns an error, read the message, fix the call and try again, or explain \
what went wrong.
- Refer to files with paths relative to the working directory.
- For tasks with several steps, first write a plan with the todos tool, keep exactly one \
task in progress, and update the list as you finish each step.
- When the user states a lasting preference or an important fact about themselves or the \
project, save it with the memory tool (a short key and a one-sentence value). Follow the \
facts listed under Memory.
- Keep answers concise and direct. When you have what you need, answer without calling \
more tools."""


def build_system_prompt(
    cwd: Path,
    instructions: ProjectInstructions | None = None,
    memory: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
    today: date | None = None,
) -> str:
    """Identity, guidelines, environment, then saved memory and ``AGENTS.md`` when present."""
    system = platform_name or f"{platform.system()} {platform.release()}".strip()
    environment = (
        "# Environment\n"
        f"- Working directory: {cwd}\n"
        f"- Operating system: {system}\n"
        f"- Date: {(today or date.today()).isoformat()}"
    )
    sections = [IDENTITY, GUIDELINES, environment]
    if memory:
        facts = "\n".join(f"- {key}: {value}" for key, value in memory.items())
        sections.append(f"# Memory (saved in earlier sessions)\n{facts}")
    if instructions is not None:
        sections.append(
            f"# Project instructions (from {instructions.path})\n"
            "Follow these instructions from the project's AGENTS.md:\n\n"
            f"{instructions.content.strip()}"
        )
    return "\n\n".join(sections)
