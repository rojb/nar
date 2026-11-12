from pathlib import Path

import pytest

from nar.skills import (
    Skill,
    SkillError,
    discover_skills,
    parse_skill,
    project_skills_dir,
    read_instructions,
    user_skills_dir,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "narhome"
    monkeypatch.setenv("NAR_HOME", str(path))
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def write_skill(root: Path, name: str, content: str | None = None) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    if content is None:
        content = f"---\nname: {name}\ndescription: About {name}\n---\nInstructions for {name}.\n"
    (directory / "SKILL.md").write_text(content, encoding="utf-8")
    return directory


def discover(project: Path):
    warnings: list[str] = []
    return discover_skills(project, warnings.append), warnings


def test_user_and_project_skills_are_found_with_their_origin(home, project):
    write_skill(home / "skills", "release")
    write_skill(project / ".nar" / "skills", "demo")

    skills, warnings = discover(project)

    assert [(s.name, s.origin) for s in skills] == [("demo", "project"), ("release", "user")]
    assert skills[0].description == "About demo"
    assert skills[0].folder == project / ".nar" / "skills" / "demo"
    assert skills[0].file == skills[0].folder / "SKILL.md"
    assert warnings == []


def test_nar_home_replaces_the_default_user_folder(home, project):
    assert user_skills_dir() == home / "skills"
    assert project_skills_dir(project) == project / ".nar" / "skills"
    write_skill(home / "skills", "demo")

    skills, _ = discover(project)

    assert [s.name for s in skills] == ["demo"]


def test_default_user_folder_is_under_the_home_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("NAR_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert user_skills_dir() == tmp_path / ".nar" / "skills"


def test_missing_folders_give_no_skills_and_no_warning(home, project):
    skills, warnings = discover(project)

    assert skills == ()
    assert warnings == []


def test_other_entries_are_ignored_silently(home, project):
    root = project / ".nar" / "skills"
    (root / "empty").mkdir(parents=True)
    (root / "README.md").write_text("not a skill")
    (root / "SKILL.md").write_text("---\nname: skills\ndescription: x\n---\n")
    (root / "odd" / "SKILL.md").mkdir(parents=True)  # a folder named like the file
    write_skill(root / "group", "deep")  # one level too deep
    write_skill(root, "demo")

    skills, warnings = discover(project)

    assert [s.name for s in skills] == ["demo"]
    assert warnings == []


def test_skills_path_that_is_a_file_is_ignored(home, project):
    (project / ".nar").mkdir()
    (project / ".nar" / "skills").write_text("x")

    assert discover(project) == ((), [])


def test_project_skill_replaces_user_skill_with_the_same_name(home, project):
    write_skill(home / "skills", "demo", "---\nname: demo\ndescription: User one\n---\nUSER")
    write_skill(home / "skills", "alpha")
    write_skill(project / ".nar" / "skills", "demo", "---\nname: demo\ndescription: P\n---\nPROJ")

    skills, warnings = discover(project)

    assert [(s.name, s.origin, s.description) for s in skills] == [
        ("alpha", "user", "About alpha"),
        ("demo", "project", "P"),
    ]
    assert read_instructions(skills[1]) == "PROJ"
    assert warnings == []


def test_skills_are_sorted_by_name(home, project):
    for name in ("zeta", "beta", "alpha"):
        write_skill(project / ".nar" / "skills", name)

    skills, _ = discover(project)

    assert [s.name for s in skills] == ["alpha", "beta", "zeta"]


def test_extra_keys_are_ignored(home, project):
    content = "---\nname: demo\ndescription: Shows\nlicense: MIT\nmetadata:\n  a: 1\n---\nBody"
    write_skill(project / ".nar" / "skills", "demo", content)

    skills, warnings = discover(project)

    assert [s.name for s in skills] == ["demo"]
    assert warnings == []


def test_quoted_folded_and_multiline_descriptions(home, project):
    root = project / ".nar" / "skills"
    write_skill(root, "quoted", '---\nname: quoted\ndescription: "Use it: always # now"\n---\n')
    write_skill(root, "folded", "---\nname: folded\ndescription: >\n  one\n  two\n---\n")
    write_skill(root, "literal", "---\nname: literal\ndescription: |\n  one\n  two\n---\n")

    skills, warnings = discover(project)

    assert {s.name: s.description for s in skills} == {
        "folded": "one two",
        "literal": "one\ntwo",
        "quoted": "Use it: always # now",
    }
    assert warnings == []


def test_crlf_files_and_a_byte_order_mark_are_accepted(home, project):
    directory = project / ".nar" / "skills" / "demo"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_bytes(
        b"\xef\xbb\xbf---\r\nname: demo\r\ndescription: D\r\n---\r\nBody\r\n"
    )

    skills, warnings = discover(project)

    assert [s.name for s in skills] == ["demo"]
    assert read_instructions(skills[0]) == "Body"
    assert warnings == []


BROKEN = {
    "no frontmatter": ("just text\n", "frontmatter is missing"),
    "empty file": ("", "frontmatter is missing"),
    "unclosed": ("---\nname: x\ndescription: y\n", "not closed"),
    "bad yaml": ("---\nname: [unclosed\n---\n", "not valid YAML"),
    "not a mapping": ("---\n- a\n- b\n---\n", "mapping"),
    "empty block": ("---\n---\nBody", "mapping"),
    "no name": ("---\ndescription: y\n---\n", "no name"),
    "empty name": ("---\nname: ''\ndescription: y\n---\n", "no name"),
    "no description": ("---\nname: x\n---\n", "no description"),
    "empty description": ("---\nname: x\ndescription: '  '\n---\n", "no description"),
    "name not a string": ("---\nname: 12\ndescription: y\n---\n", "must be a string"),
    "uppercase name": ("---\nname: Bad\ndescription: y\n---\n", "invalid name"),
    "underscore name": ("---\nname: bad_name\ndescription: y\n---\n", "invalid name"),
    "long name": (f"---\nname: {'a' * 65}\ndescription: y\n---\n", "invalid name"),
    "other name": ("---\nname: other\ndescription: y\n---\n", "does not match the folder"),
    "long description": (f"---\nname: x\ndescription: {'d' * 1025}\n---\n", "too long"),
}


@pytest.mark.parametrize(("content", "reason"), BROKEN.values(), ids=BROKEN.keys())
def test_broken_skill_gives_one_warning_and_the_others_load(home, project, content, reason):
    root = project / ".nar" / "skills"
    write_skill(root, "good")
    broken = write_skill(root, "x", content)

    skills, warnings = discover(project)

    assert [s.name for s in skills] == ["good"]
    assert len(warnings) == 1
    assert str(broken / "SKILL.md") in warnings[0]
    assert reason in warnings[0]


def test_a_name_of_64_characters_and_a_description_of_1024_are_valid(home, project):
    name = "a" * 64
    write_skill(
        project / ".nar" / "skills", name, f"---\nname: {name}\ndescription: {'d' * 1024}\n---\n"
    )

    skills, warnings = discover(project)

    assert [s.name for s in skills] == [name]
    assert warnings == []


def test_a_file_that_is_not_utf8_is_skipped_with_a_warning(home, project):
    directory = write_skill(project / ".nar" / "skills", "bad")
    (directory / "SKILL.md").write_bytes(b"---\nname: bad\ndescription: \xff\xfe\n---\n")

    skills, warnings = discover(project)

    assert skills == ()
    assert len(warnings) == 1
    assert str(directory / "SKILL.md") in warnings[0]
    assert "UTF-8" in warnings[0]


def test_a_broken_project_skill_does_not_hide_a_valid_user_skill(home, project):
    write_skill(home / "skills", "demo")
    write_skill(project / ".nar" / "skills", "demo", "no frontmatter")

    skills, warnings = discover(project)

    assert [(s.name, s.origin) for s in skills] == [("demo", "user")]
    assert len(warnings) == 1


def test_parse_skill_raises_a_skill_error_naming_the_file(tmp_path):
    folder = write_skill(tmp_path, "demo", "nothing")

    with pytest.raises(SkillError) as info:
        parse_skill(folder, "project")

    assert info.value.file == folder / "SKILL.md"
    assert str(folder / "SKILL.md") in str(info.value)


def test_read_instructions_returns_the_stripped_body(tmp_path):
    folder = write_skill(
        tmp_path, "demo", "---\nname: demo\ndescription: D\n---\n\n# Title\n\nDo this.\n\n"
    )

    skill = parse_skill(folder, "user")

    assert isinstance(skill, Skill)
    assert read_instructions(skill) == "# Title\n\nDo this."


def test_read_instructions_reads_the_file_again(tmp_path):
    folder = write_skill(tmp_path, "demo", "---\nname: demo\ndescription: D\n---\nold")
    skill = parse_skill(folder, "user")
    (folder / "SKILL.md").write_text("---\nname: demo\ndescription: D\n---\nnew", encoding="utf-8")

    assert read_instructions(skill) == "new"


def test_read_instructions_raises_once_the_file_is_deleted(tmp_path):
    folder = write_skill(tmp_path, "demo")
    skill = parse_skill(folder, "user")
    (folder / "SKILL.md").unlink()

    with pytest.raises(SkillError) as info:
        read_instructions(skill)

    assert str(folder / "SKILL.md") in str(info.value)


def test_read_instructions_raises_when_the_file_became_invalid(tmp_path):
    folder = write_skill(tmp_path, "demo")
    skill = parse_skill(folder, "user")
    (folder / "SKILL.md").write_text("broken", encoding="utf-8")

    with pytest.raises(SkillError):
        read_instructions(skill)


def test_the_example_skill_is_valid():
    examples = Path(__file__).resolve().parent.parent / "examples" / "skills"

    skill = parse_skill(examples / "changelog", "project")

    assert skill.name == "changelog"
    assert (skill.folder / "template.md").is_file()
    assert "template.md" in read_instructions(skill)
