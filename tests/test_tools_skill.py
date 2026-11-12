from pathlib import Path

import pytest

from nar.skills import parse_skill
from nar.tools import ToolContext
from nar.tools.skill import SkillTool


@pytest.fixture
def context(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


def make_skill(root: Path, name: str, body: str = "Do the thing.") -> object:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: About {name}\n---\n{body}\n", encoding="utf-8"
    )
    return parse_skill(folder, "project")


def test_exposes_the_tool_contract():
    tool = SkillTool([])

    assert tool.name == "skill"
    assert tool.parameters["required"] == ["name"]
    assert tool.parameters["additionalProperties"] is False
    assert tool.requires_approval is False


def test_returns_the_instructions_and_the_absolute_folder(tmp_path, context):
    skill = make_skill(tmp_path / "skills", "demo", "# Steps\n\n1. Do it.")

    result = SkillTool([skill]).execute({"name": "demo"}, context)

    assert not result.is_error
    assert result.output == (
        f"Skill: demo\nFolder: {(tmp_path / 'skills' / 'demo').resolve()}\n\n# Steps\n\n1. Do it."
    )
    assert "---" not in result.output
    assert Path(result.output.splitlines()[1].removeprefix("Folder: ")).is_absolute()


def test_picks_the_named_skill(tmp_path, context):
    skills = [make_skill(tmp_path, "one", "first"), make_skill(tmp_path, "two", "second")]

    result = SkillTool(skills).execute({"name": "two"}, context)

    assert "Skill: two" in result.output
    assert "second" in result.output
    assert "first" not in result.output


def test_unknown_name_lists_the_available_names(tmp_path, context):
    skills = [make_skill(tmp_path, "zeta"), make_skill(tmp_path, "alpha")]

    result = SkillTool(skills).execute({"name": "nope"}, context)

    assert result.is_error
    assert "nope" in result.output
    assert result.output.endswith("available skills: alpha, zeta")


def test_instructions_are_read_when_the_tool_is_called(tmp_path, context):
    skill = make_skill(tmp_path, "demo", "old")
    tool = SkillTool([skill])
    skill.file.write_text("---\nname: demo\ndescription: D\n---\nnew", encoding="utf-8")

    assert tool.execute({"name": "demo"}, context).output.endswith("\n\nnew")


def test_deleted_file_gives_an_error_naming_it(tmp_path, context):
    skill = make_skill(tmp_path, "demo")
    skill.file.unlink()

    result = SkillTool([skill]).execute({"name": "demo"}, context)

    assert result.is_error
    assert str(skill.file) in result.output


def test_file_that_became_invalid_gives_an_error(tmp_path, context):
    skill = make_skill(tmp_path, "demo")
    skill.file.write_text("no frontmatter", encoding="utf-8")

    result = SkillTool([skill]).execute({"name": "demo"}, context)

    assert result.is_error
    assert str(skill.file) in result.output
