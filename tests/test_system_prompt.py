from datetime import date
from pathlib import Path

from nar.agent.prompt import build_system_prompt
from nar.context import ProjectInstructions

CWD = Path("/home/user/project")


def build(instructions=None, memory=None):
    return build_system_prompt(
        CWD, instructions, memory, platform_name="Linux 6.8", today=date(2026, 10, 27)
    )


def test_identity_and_environment():
    prompt = build()
    assert "You are nar" in prompt
    assert f"Working directory: {CWD}" in prompt
    assert "Operating system: Linux 6.8" in prompt
    assert "Date: 2026-10-27" in prompt
    assert "search with glob (file names) and grep (file contents)" in prompt


def test_project_instructions_included_under_heading():
    path = CWD / "AGENTS.md"
    prompt = build(ProjectInstructions(path, "Always answer in Spanish\n", truncated=False))
    heading = f"# Project instructions (from {path})"
    assert heading in prompt
    assert prompt.index(heading) < prompt.index("Always answer in Spanish")


def test_no_project_instructions_section_without_agents_md():
    assert "Project instructions" not in build()


def test_defaults_detect_platform_and_date():
    prompt = build_system_prompt(CWD)
    assert f"Date: {date.today().isoformat()}" in prompt
    assert "Operating system: " in prompt


def test_memory_section_between_environment_and_project_instructions():
    instructions = ProjectInstructions(CWD / "AGENTS.md", "Use tabs", truncated=False)
    prompt = build(instructions, {"language": "Answer in Spanish", "test_runner": "pytest"})
    section = (
        "# Memory (saved in earlier sessions)\n- language: Answer in Spanish\n- test_runner: pytest"
    )
    assert section in prompt
    assert prompt.index("# Environment") < prompt.index(section) < prompt.index("Use tabs")


def test_no_memory_section_without_entries():
    assert "# Memory" not in build(memory={})
    assert "# Memory" not in build()


def test_planning_and_memory_guidelines():
    prompt = build()
    assert "first write a plan with the todos tool" in prompt
    assert "save it with the memory tool" in prompt
