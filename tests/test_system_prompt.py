from datetime import date
from pathlib import Path

from nar.agent.prompt import build_subagent_prompt, build_system_prompt
from nar.config import Language
from nar.context import ProjectInstructions
from nar.skills import Skill, parse_skill

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


def subagent(instructions=None):
    return build_subagent_prompt(
        "THE ROLE", CWD, instructions, platform_name="Linux 6.8", today=date(2026, 10, 27)
    )


def test_main_prompt_tells_when_to_delegate():
    text = " ".join(build().split())
    assert "use the task tool with the explorer subagent" in text
    assert "Do not use it to read a file you already know" in text


def test_subagent_prompt_has_the_role_and_the_environment():
    text = subagent()
    assert "You are nar" in text
    assert "THE ROLE" in text
    assert str(CWD) in text
    assert "Linux 6.8" in text


def test_subagent_prompt_includes_project_instructions():
    instructions = ProjectInstructions(Path("/p/AGENTS.md"), "Always answer in Spanish", False)
    assert "Always answer in Spanish" in subagent(instructions)


def test_subagent_prompt_leaves_out_memory_and_the_main_guidelines():
    text = subagent()
    assert "Memory" not in text
    assert "todos" not in text
    assert "task tool" not in text


def test_the_reply_language_is_spanish_by_default():
    prompt = build()
    assert "# Language\nAlways reply to the user in Spanish" in prompt
    assert "English" not in prompt.split("# Language")[1].split("# ")[0]


def test_the_reply_language_can_be_english():
    prompt = build_system_prompt(CWD, language=Language.EN)
    assert "# Language\nAlways reply to the user in English" in prompt
    assert "Spanish" not in prompt.split("# Language")[1].split("# ")[0]


def test_the_language_rule_keeps_code_paths_commands_and_quotes_unchanged():
    text = " ".join(build().split())
    for kept in ("code", "identifiers", "file names", "paths", "commands", "tool arguments"):
        assert kept in text.split("Always reply to the user")[1]
    assert "quoted text unchanged" in text


def test_the_language_section_comes_before_memory_and_project_instructions():
    instructions = ProjectInstructions(CWD / "AGENTS.md", "Use tabs", truncated=False)
    prompt = build(instructions, {"editor": "vim"})
    assert prompt.index("# Environment") < prompt.index("# Language")
    assert prompt.index("# Language") < prompt.index("# Memory") < prompt.index("Use tabs")


def test_the_subagent_prompt_carries_the_same_language_rule():
    assert "Always reply to the user in Spanish" in subagent()
    english = build_subagent_prompt("THE ROLE", CWD, language=Language.EN)
    assert "# Language\nAlways reply to the user in English" in english
    assert "Spanish" not in english


def demo_skills():
    return [
        Skill("demo", "Shows how skills work", Path("/skills/demo"), "project"),
        Skill("release", "Cuts a release", Path("/skills/release"), "user"),
    ]


def test_skills_are_listed_with_their_descriptions_in_the_given_order():
    prompt = build_system_prompt(CWD, skills=demo_skills())

    assert "# Skills" in prompt
    assert "- demo: Shows how skills work\n- release: Cuts a release" in prompt
    assert "call the skill tool" in prompt
    assert "matches" in prompt


def test_skills_section_comes_after_memory_and_project_instructions():
    instructions = ProjectInstructions(CWD / "AGENTS.md", "Be brief", truncated=False)

    prompt = build_system_prompt(CWD, instructions, {"k": "v"}, demo_skills())

    assert prompt.index("# Memory") < prompt.index("# Project instructions")
    assert prompt.index("# Project instructions") < prompt.index("# Skills")


def test_skills_section_never_holds_the_instructions(tmp_path):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Short\n---\nSECRET-INSTRUCTIONS", encoding="utf-8"
    )

    prompt = build_system_prompt(CWD, skills=[parse_skill(folder, "project")])

    assert "- demo: Short" in prompt
    assert "SECRET-INSTRUCTIONS" not in prompt


def test_no_skills_section_without_skills():
    assert "# Skills" not in build()
    assert "# Skills" not in build_system_prompt(CWD, skills=())


def test_subagent_prompt_has_no_skills_section():
    assert "# Skills" not in subagent()
