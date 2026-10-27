import importlib.util
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

SCRIPT = Path(__file__).parent.parent / "scripts" / "run_tool.py"


@pytest.fixture(scope="module")
def run_tool():
    spec = importlib.util.spec_from_file_location("run_tool", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run


def test_reads_a_file_and_shows_the_call(run_tool, tmp_path):
    path = tmp_path / "data.txt"
    path.write_text("".join(f"row {n}\n" for n in range(1, 11)), encoding="utf-8")
    arguments = json.dumps({"path": str(path), "limit": 5})

    result = CliRunner().invoke(run_tool, ["read_file", arguments])

    assert result.exit_code == 0, result.output
    assert "read_file(" in result.output
    assert "read 5 lines" in result.output
    assert "[Showing lines 1-5 of 10]" in result.output
    assert "1\trow 1" in result.output
    assert "5\trow 5" in result.output
    assert "row 6" not in result.output


def test_invalid_json_exits_2(run_tool):
    result = CliRunner().invoke(run_tool, ["read_file", "{not json"])

    assert result.exit_code == 2
    assert "error: arguments are not valid JSON:" in result.output


def test_non_object_json_exits_2(run_tool):
    result = CliRunner().invoke(run_tool, ["read_file", "[1, 2]"])

    assert result.exit_code == 2
    assert "arguments are not valid JSON" in result.output


def test_unknown_tool_exits_1_and_lists_available(run_tool):
    result = CliRunner().invoke(run_tool, ["delete_everything", "{}"])

    assert result.exit_code == 1
    assert "does not exist" in result.output
    assert "read_file" in result.output


def test_default_arguments_are_empty_object(run_tool):
    result = CliRunner().invoke(run_tool, ["read_file"])

    assert result.exit_code == 1
    assert "missing required argument 'path'" in result.output
