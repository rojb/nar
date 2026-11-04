import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from nar.tools import ToolContext, ToolRegistry, create_default_registry
from nar.tools.custom import (
    TrustStore,
    find_tool_files,
    fingerprint,
    load_custom_tools,
    load_tool_file,
)

GOOD = """
from nar.tools import Tool, ToolResult


class {cls}(Tool):
    name = "{name}"
    description = "A tool."
    parameters = {{"type": "object", "properties": {{}}}}

    def execute(self, arguments, context):
        return ToolResult.ok("from {name}")
"""

MARKING = """
from pathlib import Path

Path({marker!r}).write_text("ran")
"""


def write(folder, name, text):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def good(name="custom_one", cls="CustomOne"):
    return GOOD.format(name=name, cls=cls)


class Question:
    def __init__(self, can_ask=True, answer=True):
        self.can_ask = can_ask
        self.answer = answer
        self.asked = []

    def available(self):
        return self.can_ask

    def ask(self, folder, files):
        self.asked.append([path.name for path in files])
        return self.answer


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("NAR_HOME", str(home))
    return home, project


def run(project, question=None, trust=False):
    registry = ToolRegistry(ToolContext(project))
    notes, warnings = [], []
    question = question or Question()
    load_custom_tools(registry, project, question, trust, notes.append, warnings.append)
    return registry, notes, warnings, question


# Finding and loading


def test_files_are_found_by_name_and_helpers_are_skipped(tmp_path):
    for name in ("b.py", "a.py", "_helper.py", "notes.txt"):
        write(tmp_path, name, "")
    (tmp_path / "folder.py").mkdir()
    assert [p.name for p in find_tool_files(tmp_path)] == ["a.py", "b.py"]
    assert find_tool_files(tmp_path / "missing") == []


def test_imported_and_abstract_classes_do_not_count(tmp_path):
    path = write(
        tmp_path,
        "t.py",
        "from nar.tools.read_file import ReadFileTool\nfrom nar.tools import Tool\n"
        "class Base(Tool):\n    pass\n" + good(),
    )
    tools, problems = load_tool_file(path)
    assert [tool.name for tool in tools] == ["custom_one"]
    assert problems == []


def test_a_file_can_define_several_tools(tmp_path):
    text = good("one", "One") + good("two", "Two")
    tools, _ = load_tool_file(write(tmp_path, "t.py", text))
    assert sorted(tool.name for tool in tools) == ["one", "two"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("def broken(:\n", "SyntaxError"),
        ("import not_a_real_module_xyz\n", "ModuleNotFoundError"),
        ("raise RuntimeError('boom')\n", "RuntimeError: boom"),
        ("import sys\nsys.exit(1)\n", "SystemExit"),
        ("x = 1\n", "defines no Tool"),
        (
            "from nar.tools import Tool, ToolResult\nclass T(Tool):\n"
            "    def __init__(self, needed):\n        pass\n"
            "    def execute(self, arguments, context):\n        pass\n",
            "cannot create T",
        ),
    ],
)
def test_every_failure_is_reported_and_the_others_still_load(env, text, expected):
    home, project = env
    write(home / "tools", "a_bad.py", text)
    write(home / "tools", "b_good.py", good())
    registry, _, warnings, _ = run(project)
    assert registry.names() == ["custom_one"]
    assert len(warnings) == 1
    assert "a_bad.py" in warnings[0]
    assert expected in warnings[0]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            'name = "bad name"\n    description = "d"\n    parameters = {"type": "object"}',
            "invalid",
        ),
        ('name = "x"\n    description = "d"\n    parameters = {"type": "array"}', "JSON Schema"),
        ('description = "d"\n    parameters = {"type": "object"}', "does not set name"),
        ('name = "x"\n    parameters = {"type": "object"}', "does not set description"),
        ('name = "x"\n    description = 5\n    parameters = {"type": "object"}', "must be text"),
        ('name = "x"\n    description = "d"', "does not set parameters"),
    ],
)
def test_a_tool_that_would_break_later_is_rejected_when_it_loads(env, body, expected):
    home, project = env
    text = (
        "from nar.tools import Tool\nclass T(Tool):\n"
        f"    {body}\n    def execute(self, arguments, context):\n        pass\n"
    )
    write(home / "tools", "t.py", text)
    registry, _, warnings, _ = run(project)
    assert registry.names() == []
    assert expected in warnings[0]
    registry.definitions()  # still usable on every model call


def test_ctrl_c_while_loading_gets_out(env):
    home, project = env
    write(home / "tools", "t.py", "raise KeyboardInterrupt\n")
    with pytest.raises(KeyboardInterrupt):
        run(project)


def test_two_files_with_the_same_name_do_not_collide(env):
    home, project = env
    write(home / "tools", "same.py", good("global_one", "A"))
    write(project / ".nar" / "tools", "same.py", good("project_one", "B"))
    registry, _, _, _ = run(project)
    assert registry.names() == ["global_one", "project_one"]
    assert registry.execute("global_one", {}).output == "from global_one"
    assert registry.execute("project_one", {}).output == "from project_one"


def test_loading_leaves_no_bytecode_and_no_module_behind(env):
    home, project = env
    write(home / "tools", "t.py", good())
    before = set(sys.modules)
    run(project)
    assert not (home / "tools" / "__pycache__").exists()
    assert not [name for name in set(sys.modules) - before if name.startswith("nar_custom_tool")]


# Confirmation


def test_global_tools_never_ask_even_without_a_terminal(env):
    home, project = env
    write(home / "tools", "t.py", good())
    question = Question(can_ask=False)
    registry, notes, warnings, _ = run(project, question)
    assert registry.names() == ["custom_one"]
    assert question.asked == []
    assert warnings == []
    assert "custom tool loaded: custom_one" in notes[0]


def test_nothing_is_said_without_tool_files(env):
    _, project = env
    _, notes, warnings, question = run(project)
    assert (notes, warnings, question.asked) == ([], [], [])


def test_yes_loads_and_the_next_run_does_not_ask(env):
    _, project = env
    write(project / ".nar" / "tools", "t.py", good())
    registry, _, _, question = run(project)
    assert question.asked == [["t.py"]]
    assert registry.names() == ["custom_one"]
    registry, _, _, question = run(project, Question(can_ask=False))
    assert question.asked == []
    assert registry.names() == ["custom_one"]


def test_no_does_not_run_the_file_and_asks_again(env, tmp_path):
    _, project = env
    marker = tmp_path / "ran"
    write(project / ".nar" / "tools", "t.py", MARKING.format(marker=str(marker)) + good())
    registry, notes, _, _ = run(project, Question(answer=False))
    assert not marker.exists()
    assert registry.names() == []
    assert "project tools skipped" in notes[0]
    _, _, _, question = run(project, Question(answer=False))
    assert question.asked


@pytest.mark.parametrize("change", ["edit", "add", "rename"])
def test_a_changed_folder_asks_again_before_running_anything(env, tmp_path, change):
    _, project = env
    folder = project / ".nar" / "tools"
    path = write(folder, "t.py", good())
    run(project)
    marker = tmp_path / "ran"
    if change == "edit":
        path.write_text(MARKING.format(marker=str(marker)) + good(), encoding="utf-8")
    elif change == "add":
        write(folder, "new.py", MARKING.format(marker=str(marker)) + good("x", "X"))
    else:
        path.rename(folder / "other.py")
    registry, _, warnings, _ = run(project, Question(can_ask=False))
    assert not marker.exists()
    assert registry.names() == []
    assert "--trust-project-tools" in warnings[0]


def test_a_file_that_cannot_be_read_only_warns(env, monkeypatch):
    _, project = env
    write(project / ".nar" / "tools", "t.py", good())

    def fail(self):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", fail)
    registry, _, warnings, question = run(project)
    assert registry.names() == []
    assert question.asked == []
    assert "Permission denied" in warnings[0]


def test_trust_is_per_folder(env, tmp_path):
    _, project = env
    write(project / ".nar" / "tools", "t.py", good())
    run(project)
    other = tmp_path / "other"
    write(other / ".nar" / "tools", "t.py", good())
    _, _, _, question = run(other)
    assert question.asked  # same contents, another folder


def test_the_flag_allows_without_asking_and_is_remembered(env):
    _, project = env
    write(project / ".nar" / "tools", "t.py", good())
    registry, _, _, question = run(project, Question(can_ask=False), trust=True)
    assert question.asked == []
    assert registry.names() == ["custom_one"]
    registry, _, warnings, _ = run(project, Question(can_ask=False))
    assert registry.names() == ["custom_one"]
    assert warnings == []


def test_the_same_folder_twice_loads_once_as_global(tmp_path, monkeypatch):
    home = tmp_path / "user"
    monkeypatch.setenv("NAR_HOME", str(home / ".nar"))
    write(home / ".nar" / "tools", "t.py", good())
    registry, _, warnings, question = run(home, Question(can_ask=False))
    assert registry.names() == ["custom_one"]
    assert warnings == []
    assert question.asked == []


def test_fingerprint_depends_on_names_and_contents(tmp_path):
    a = write(tmp_path, "a.py", "x")
    first = fingerprint([a])
    assert fingerprint([a]) == first
    a.write_text("y", encoding="utf-8")
    assert fingerprint([a]) != first
    assert fingerprint([write(tmp_path, "b.py", "y")]) != fingerprint([a])


@pytest.mark.parametrize("text", ["not json", "[]", '{"version": 9, "projects": {}}'])
def test_an_invalid_trust_file_counts_as_nothing_and_stays_until_a_project_is_allowed(
    tmp_path, text
):
    path = write(tmp_path, "trusted_tools.json", text)
    warnings = []
    store = TrustStore(path, warnings.append)
    assert store.allowed(Path("/p"), "contents") is False
    assert "ignoring" in warnings[0]
    assert path.read_text(encoding="utf-8") == text
    store.remember(Path("/p"), "contents")
    assert store.allowed(Path("/p"), "contents") is True


def test_a_trust_file_that_cannot_be_written_only_warns(env, monkeypatch):
    _, project = env
    write(project / ".nar" / "tools", "t.py", good())

    def fail(*args):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("nar.session.store.write_private", fail)
    registry, _, warnings, _ = run(project)
    assert registry.names() == ["custom_one"]
    assert "cannot remember this answer" in warnings[0]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_trust_file_is_private(tmp_path):
    path = tmp_path / "home" / "trusted_tools.json"
    TrustStore(path, print).remember(Path("/p"), "contents")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# Names


def test_built_in_tools_cannot_be_replaced_and_global_comes_before_project(env):
    home, project = env
    write(home / "tools", "g.py", good("shared", "G"))
    folder = project / ".nar" / "tools"
    write(folder, "p.py", good("shared", "P"))
    write(folder, "q.py", good("read_file", "Q"))
    registry = create_default_registry(project)
    built_in = registry.get("read_file")
    warnings = []
    load_custom_tools(registry, project, Question(), False, lambda _: None, warnings.append)
    assert registry.get("read_file") is built_in
    assert registry.execute("shared", {}).output == "from shared"
    assert type(registry.get("shared")).__name__ == "G"
    assert len(warnings) == 2
    assert "p.py" in warnings[0] and "already registered" in warnings[0]
    assert "q.py" in warnings[1]


# The example


def test_the_example_works_from_the_global_folder(env):
    home, project = env
    (home / "tools").mkdir(parents=True)
    example = Path(__file__).parent.parent / "examples" / "tools" / "word_count.py"
    shutil.copy(example, home / "tools")
    (project / "a.txt").write_text("one two\nthree\n", encoding="utf-8")
    registry, _, warnings, _ = run(project)
    assert warnings == []
    assert registry.execute("word_count", {"path": "a.txt"}).output == "2 lines, 3 words"
    assert registry.execute("word_count", {"path": "missing"}).is_error
