from nar.agent.loops import (
    FAILURE_HINT,
    Decision,
    LoopDetector,
    LoopKind,
    canonical_arguments,
    render_failure,
    warning_text,
)
from nar.providers.messages import ToolCall
from nar.tools.base import FailureKind, ToolResult

SCHEMA_WITH_ANY_KEY = {"type": "object"}


def call(name="read_file", path="a.py"):
    return ToolCall("c1", name, {"path": path})


def record(detector, *, times=1, name="read_file", path="a.py", failed=False):
    result = ToolResult.error("boom") if failed else ToolResult.ok("ok")
    for _ in range(times):
        detector.record(call(name, path), result)


def test_canonical_arguments_ignores_key_order():
    assert canonical_arguments({"a": 1, "b": 2}) == canonical_arguments({"b": 2, "a": 1})
    assert canonical_arguments({"a": 1}) != canonical_arguments({"a": 2})


def test_canonical_arguments_is_stable_for_values_json_cannot_serialize():
    arguments = {"callback": SCHEMA_WITH_ANY_KEY, "value": {1, 2}}
    assert canonical_arguments(arguments) == canonical_arguments(dict(arguments))


def test_three_identical_calls_are_a_repetition():
    detector = LoopDetector()
    record(detector, times=2)
    assert detector.check() is None
    record(detector)
    finding = detector.check()
    assert finding is not None
    assert finding.kind is LoopKind.REPEAT
    assert finding.count == 3
    assert finding.decision is Decision.WARN


def test_different_arguments_are_not_a_repetition():
    detector = LoopDetector()
    record(detector, path="a.py")
    record(detector, path="b.py")
    record(detector, path="c.py")
    assert detector.check() is None


def test_the_same_tool_on_two_paths_is_not_a_repetition():
    detector = LoopDetector()
    record(detector, times=2, path="a.py")
    record(detector, times=2, path="b.py")
    assert detector.check() is None


def test_two_identical_failing_calls_are_a_repetition():
    detector = LoopDetector()
    record(detector, failed=True)
    assert detector.check() is None
    record(detector, failed=True)
    finding = detector.check()
    assert finding is not None
    assert finding.decision is Decision.WARN


def test_two_identical_succeeding_calls_are_not_a_repetition():
    detector = LoopDetector()
    record(detector, times=2)
    assert detector.check() is None


def test_alternating_calls_are_a_repetition():
    detector = LoopDetector()
    for name in ("edit_file", "read_file", "edit_file", "read_file"):
        record(detector, name=name)
    finding = detector.check()
    assert finding is not None
    assert finding.kind is LoopKind.OSCILLATION


def test_four_identical_calls_are_a_repetition_and_not_an_oscillation():
    detector = LoopDetector()
    record(detector, times=4)
    finding = detector.check()
    assert finding is not None
    assert finding.kind is LoopKind.REPEAT


def test_broken_patterns_are_not_a_repetition():
    for names in (
        ("edit_file", "read_file", "edit_file", "write_file"),
        ("edit_file", "read_file", "read_file", "edit_file"),
        ("edit_file", "read_file", "write_file", "read_file"),
    ):
        detector = LoopDetector()
        for name in names:
            record(detector, name=name)
        assert detector.check() is None, names


def test_reset_forgets_everything():
    detector = LoopDetector()
    record(detector, times=3)
    assert detector.check() is not None
    detector.reset()
    assert detector.calls == []
    assert detector.warned is False
    assert detector.check() is None


def test_reset_tail_keeps_the_warning_and_needs_the_pattern_again():
    detector = LoopDetector()
    record(detector, times=3)
    assert detector.check().decision is Decision.WARN
    detector.reset_tail()
    assert detector.warned is True
    record(detector, times=2)
    assert detector.check() is None
    record(detector)
    assert detector.check().decision is Decision.STOP


def test_check_is_stable_when_called_twice():
    detector = LoopDetector()
    record(detector, times=3)
    assert detector.check() == detector.check()


def test_a_long_history_is_only_inspected_at_its_tail():
    detector = LoopDetector()
    for index in range(500):
        record(detector, path=f"file{index}.py")
    assert detector.check() is None
    record(detector, times=2, path="last.py")
    record(detector, path="last.py")
    assert detector.check() is not None


def test_warning_text_names_the_repeated_call_and_the_count():
    detector = LoopDetector()
    record(detector, times=3)
    text = warning_text(detector.check())
    assert text.startswith("[nar] ")
    assert "read_file" in text
    assert "three times" in text
    assert "stopped" in text


def test_warning_text_names_both_calls_of_an_alternation():
    detector = LoopDetector()
    for name in ("edit_file", "read_file", "edit_file", "read_file"):
        record(detector, name=name)
    text = warning_text(detector.check())
    assert "edit_file" in text and "read_file" in text
    assert "alternat" in text


def test_render_failure_envelopes_the_message_unchanged():
    result = ToolResult.error("pytest: command not found", FailureKind.TOOL_ERROR)
    wrapped = render_failure(call(name="shell"), result)
    assert wrapped.output == (
        f"[tool error] shell\nkind: tool error\nmessage:\npytest: command not found\n{FAILURE_HINT}"
    )
    assert wrapped.is_error is True
    assert wrapped.error_kind is FailureKind.TOOL_ERROR


def test_render_failure_names_each_kind():
    expected = {
        FailureKind.UNKNOWN_TOOL: "unknown tool",
        FailureKind.INVALID_ARGUMENTS: "invalid arguments",
        FailureKind.TOOL_ERROR: "tool error",
        FailureKind.UNEXPECTED_ERROR: "unexpected error",
    }
    for kind, label in expected.items():
        wrapped = render_failure(call(), ToolResult.error("x", kind))
        assert f"kind: {label}\n" in wrapped.output


def test_render_failure_keeps_the_other_fields():
    result = ToolResult(output="x", is_error=True, summary="s", diff="d", display="v")
    wrapped = render_failure(call(), result)
    assert (wrapped.summary, wrapped.diff, wrapped.display) == ("s", "d", "v")


def test_the_envelope_of_a_non_ascii_message_survives():
    wrapped = render_failure(call(), ToolResult.error("no se encontró el archivo"))
    assert "no se encontró el archivo" in wrapped.output
