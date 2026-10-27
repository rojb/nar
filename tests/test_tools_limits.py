import re

from nar.tools.limits import MAX_OUTPUT_CHARS, truncate_output

MARKER = re.compile(r"\n\[output truncated: (\d+) characters omitted\]$")


def test_short_output_is_unchanged():
    text = "x" * 100
    assert truncate_output(text) == text


def test_output_at_the_limit_is_unchanged():
    text = "x" * MAX_OUTPUT_CHARS
    assert truncate_output(text) == text


def test_long_output_is_cut_with_marker():
    result = truncate_output("x" * 50_000)
    assert len(result) <= MAX_OUTPUT_CHARS
    match = MARKER.search(result)
    assert match is not None
    assert int(match.group(1)) >= 20_000


def test_marker_count_matches_what_was_cut():
    text = "".join(str(i % 10) for i in range(40_000))
    result = truncate_output(text)
    match = MARKER.search(result)
    kept = result[: match.start()]
    assert text.startswith(kept)
    assert len(kept) + int(match.group(1)) == len(text)


def test_just_over_the_limit_still_fits():
    result = truncate_output("x" * (MAX_OUTPUT_CHARS + 1))
    assert len(result) <= MAX_OUTPUT_CHARS
    assert MARKER.search(result)
