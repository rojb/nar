import base64

import pytest
from mcp_types import (
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from nar.mcp.results import NO_OUTPUT, base64_size, to_tool_result


def text(value):
    return TextContent(text=value)


def result(*blocks, structured=None, is_error=False):
    return CallToolResult(content=list(blocks), structured_content=structured, is_error=is_error)


def convert(*blocks, **kwargs):
    return to_tool_result("demo", "tool", result(*blocks, **kwargs))


def b64(size):
    return base64.b64encode(b"\0" * size).decode()


def test_one_text_block():
    outcome = convert(text("hi"))
    assert outcome.output == "hi"
    assert not outcome.is_error
    assert outcome.summary == "demo: tool"


def test_two_text_blocks_are_separated_by_a_blank_line():
    assert convert(text("one"), text("two")).output == "one\n\ntwo"


def test_image_is_described_not_forwarded():
    image = ImageContent(data=b64(40_000), mime_type="image/png")
    output = convert(text("chart"), image).output
    assert output == "chart\n\n[image image/png, 40000 bytes, not shown]"
    assert "AAAA" not in output


def test_audio_is_described():
    audio = AudioContent(data=b64(10), mime_type="audio/wav")
    assert convert(audio).output == "[audio audio/wav, 10 bytes, not shown]"


def test_resource_link():
    link = ResourceLink(name="a.txt", uri="file:///a.txt")
    assert convert(link).output == "[resource link: a.txt file:///a.txt]"


def test_embedded_text_resource_gives_its_text():
    resource = EmbeddedResource(
        resource=TextResourceContents(uri="file:///a.txt", text="contents of a")
    )
    assert convert(resource).output == "contents of a"


def test_embedded_binary_resource_is_described():
    resource = EmbeddedResource(
        resource=BlobResourceContents(uri="file:///b.bin", mime_type="application/zip", blob=b64(7))
    )
    output = convert(resource).output
    assert output == "[binary resource file:///b.bin application/zip, 7 bytes, not shown]"


def test_structured_content_only_is_given_as_json():
    assert convert(structured={"count": 3}).output == '{"count": 3}'


def test_structured_content_is_not_added_to_text():
    assert convert(text("three"), structured={"count": 3}).output == "three"


def test_empty_result_says_no_output():
    assert convert().output == NO_OUTPUT


def test_error_result_carries_the_text():
    outcome = convert(text("project not found"), is_error=True)
    assert outcome.is_error
    assert outcome.output == "project not found"


def test_error_result_without_text_still_says_something():
    outcome = convert(is_error=True)
    assert outcome.is_error
    assert "demo: tool" in outcome.output


def test_non_ascii_text_is_intact():
    assert convert(text("café 日本")).output == "café 日本"


def test_structured_non_ascii_is_not_escaped():
    assert convert(structured={"name": "café"}).output == '{"name": "café"}'


@pytest.mark.parametrize("size", [0, 1, 2, 3, 4, 5, 100, 40_000])
def test_size_from_base64_length_with_padding(size):
    assert base64_size(b64(size)) == size


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 100, 40_000])
def test_size_from_base64_length_without_padding(size):
    assert base64_size(b64(size).rstrip("=")) == size
