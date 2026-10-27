from pathlib import Path

import httpx
import pytest

from nar.tools import ToolContext, web
from nar.tools.registry import ToolRegistry
from nar.tools.web_fetch import MAX_DOWNLOAD_BYTES, WebFetchTool, html_to_markdown

DOCS = """<!doctype html>
<html><head><title>  HTTPX
  Docs </title><style>.x{color:red}</style></head>
<body>
  <nav><a href="/">Home</a> menu items</nav>
  <script>var tracking = 1;</script>
  <h1>Install</h1>
  <p>Read the <a href="/guide">user guide</a> first.</p>
  <ul><li>sync</li><li>async</li></ul>
  <pre><code>pip install httpx</code></pre>
  <footer>Copyright footer text</footer>
</body></html>"""


@pytest.fixture
def serve(monkeypatch):
    """Answer every request with ``handler``; returns the list of requests made."""
    requests = []

    def install(handler):
        def recording(request):
            requests.append(request)
            return handler(request)

        real = web.http_client
        monkeypatch.setattr(web, "http_client", lambda: real(httpx.MockTransport(recording)))
        return requests

    return install


def fetch(url):
    return WebFetchTool().execute({"url": url}, ToolContext(cwd=Path.cwd()))


def html_response(text, status=200):
    return httpx.Response(status, text=text, headers={"content-type": "text/html; charset=utf-8"})


def test_exposes_the_tool_contract():
    tool = WebFetchTool()
    assert tool.name == "web_fetch"
    assert tool.parameters["required"] == ["url"]
    assert tool.requires_approval is False


def test_page_converted_to_markdown_with_title_and_url(serve):
    serve(lambda request: html_response(DOCS))
    result = fetch("https://docs.example.test/start")
    assert not result.is_error
    output = result.output
    assert output.startswith("# HTTPX Docs\nURL: https://docs.example.test/start\n\n# Install")
    assert "[user guide](https://docs.example.test/guide)" in output
    assert "- sync\n- async" in output
    assert "```\npip install httpx\n```" in output
    for noise in ("menu items", "tracking", "Copyright footer", "color:red"):
        assert noise not in output
    assert result.summary.startswith("fetched ")
    assert result.summary.endswith(" chars from docs.example.test")


def test_redirect_followed_and_final_url_shown(serve):
    def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://example.test/new"})
        return html_response("<title>New</title><p>moved here</p>")

    serve(handler)
    output = fetch("https://example.test/old").output
    assert output.startswith("# New\nURL: https://example.test/new\n")
    assert "moved here" in output


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "example.com"])
def test_unsupported_schemes_make_no_request(serve, url):
    requests = serve(lambda request: html_response("x"))
    result = fetch(url)
    assert result.is_error
    assert result.output == f"only http and https URLs are supported: {url}"
    assert requests == []


def test_json_returned_as_text(serve):
    serve(lambda request: httpx.Response(200, json={"version": "0.28.1"}))
    output = fetch("https://api.example.test/v").output
    assert output == 'URL: https://api.example.test/v\n\n{"version":"0.28.1"}'


def test_plain_text_and_markdown_returned_as_text(serve):
    readme = "# Title\n\nSome *markdown*.\n"
    serve(
        lambda request: httpx.Response(200, text=readme, headers={"content-type": "text/markdown"})
    )
    assert fetch("https://example.test/README.md").output.endswith("# Title\n\nSome *markdown*.")


def test_pdf_rejected(serve):
    pdf = httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})
    serve(lambda request: pdf)
    result = fetch("https://example.test/manual.pdf")
    assert result.is_error
    assert result.output == (
        "content type application/pdf is not supported: https://example.test/manual.pdf"
    )


def test_missing_content_type_is_sniffed(serve):
    serve(lambda request: httpx.Response(200, content=b"<html><title>T</title><p>hi</p></html>"))
    assert fetch("https://example.test/").output == "# T\nURL: https://example.test/\n\nhi"


def test_missing_content_type_binary_rejected(serve):
    serve(lambda request: httpx.Response(200, content=b"\x89PNG\r\n\x1a\n\xff\xfe"))
    result = fetch("https://example.test/img")
    assert result.is_error
    assert "content type unknown is not supported" in result.output


def test_long_page_truncated_by_registry(serve):
    serve(lambda request: html_response("<p>" + "word " * 16_000 + "</p>"))
    registry = ToolRegistry()
    registry.register(WebFetchTool())
    result = registry.execute("web_fetch", {"url": "https://example.test/long"})
    assert not result.is_error
    assert len(result.output) <= 30_000
    assert "[output truncated:" in result.output


def test_download_stops_at_limit(serve, monkeypatch):
    monkeypatch.setattr("nar.tools.web_fetch.MAX_DOWNLOAD_BYTES", 10)
    serve(lambda request: httpx.Response(200, text="0123456789ABCDEF"))
    output = fetch("https://example.test/big.txt").output
    assert output.endswith("0123456789\n[download stopped at 5 MB]")
    assert MAX_DOWNLOAD_BYTES == 5 * 1024 * 1024


def test_http_error_names_status_and_url(serve):
    serve(lambda request: html_response("not here", status=404))
    result = fetch("https://example.test/missing")
    assert result.is_error
    assert result.output == "HTTP 404 for https://example.test/missing"


def test_timeout(serve):
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    serve(slow)
    assert fetch("https://slow.example.test/").output == (
        "request to https://slow.example.test/ timed out after 20 s"
    )


def test_unknown_host(serve):
    def unknown(request):
        raise httpx.ConnectError("[Errno -2] Name or service not known", request=request)

    serve(unknown)
    result = fetch("https://no-such-host.invalid/")
    assert result.is_error
    assert result.output.startswith("could not reach https://no-such-host.invalid/: ")


def test_html_to_markdown_without_title():
    title, markdown = html_to_markdown("<p>Just <b>text</b></p>", "https://example.test/")
    assert title is None
    assert markdown == "Just **text**"
