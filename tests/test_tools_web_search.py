from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from nar.tools import ToolContext, web
from nar.tools.web_search import REFUSED, WebSearchTool, parse_results


def result_html(title, href, snippet, ad=False):
    kind = "result results_links web-result" + (" result--ad" if ad else "")
    return f"""
    <div class="{kind}">
      <div class="links_main result__body">
        <h2 class="result__title"><a class="result__a" href="{href}">{title}</a></h2>
        <a class="result__snippet" href="{href}">{snippet}</a>
      </div>
    </div>"""


def page(*results):
    body = "".join(results)
    return f'<html><body><div class="results">{body}</div></body></html>'


TEN = page(
    *(
        result_html(f"Result {i}", f"https://example.test/{i}", f"Snippet <b>{i}</b>")
        for i in range(1, 11)
    )
)


@pytest.fixture
def serve(monkeypatch):
    """Answer DuckDuckGo requests with ``handler``; returns the list of requests made."""
    requests = []

    def install(handler):
        def recording(request):
            requests.append(request)
            return handler(request)

        real = web.http_client
        monkeypatch.setattr(web, "http_client", lambda: real(httpx.MockTransport(recording)))
        return requests

    return install


def search(**arguments):
    return WebSearchTool().execute(arguments, ToolContext(cwd=Path.cwd()))


def test_exposes_the_tool_contract():
    tool = WebSearchTool()
    assert tool.name == "web_search"
    assert tool.parameters["required"] == ["query"]
    assert tool.parameters["properties"]["max_results"]["maximum"] == 10
    assert tool.requires_approval is False


def test_results_numbered_with_title_url_and_snippet(serve):
    requests = serve(lambda request: httpx.Response(200, text=TEN))
    result = search(query="httpx python documentation", max_results=2)
    assert not result.is_error
    assert result.output == (
        "1. Result 1\n   https://example.test/1\n   Snippet 1\n\n"
        "2. Result 2\n   https://example.test/2\n   Snippet 2"
    )
    assert result.summary == "2 results"
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://html.duckduckgo.com/html/"
    assert parse_qs(request.content.decode()) == {"q": ["httpx python documentation"]}


def test_default_is_five_results(serve):
    serve(lambda request: httpx.Response(200, text=TEN))
    output = search(query="x").output
    assert "5. Result 5" in output
    assert "6. Result 6" not in output


def test_ads_skipped_and_redirect_links_decoded():
    html = page(
        result_html("Ad", "https://ads.example/x", "buy", ad=True),
        result_html(
            "Docs",
            "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python-httpx.org%2Fapi%2F&amp;rut=abc",
            "API reference",
        ),
        result_html("Relative", "//example.test/page", ""),
    )
    results = parse_results(html)
    assert [r.title for r in results] == ["Docs", "Relative"]
    assert results[0].url == "https://www.python-httpx.org/api/"
    assert results[1].url == "https://example.test/page"


def test_no_results(serve):
    serve(lambda request: httpx.Response(200, text=page()))
    result = search(query="zzqx")
    assert not result.is_error
    assert result.output == "(no results for zzqx)"


@pytest.mark.parametrize("status", [202, 403, 429])
def test_refusal_statuses(serve, status):
    serve(lambda request: httpx.Response(status, text="<html></html>"))
    result = search(query="x")
    assert result.is_error
    assert result.output == REFUSED


def test_challenge_page_is_a_refusal(serve):
    challenge = '<html><body><form id="challenge-form" class="anomaly-modal"></form></body></html>'
    serve(lambda request: httpx.Response(200, text=challenge))
    result = search(query="x")
    assert result.is_error
    assert "try again later" in result.output


def test_other_http_error(serve):
    serve(lambda request: httpx.Response(500, text="boom"))
    assert search(query="x").output == "DuckDuckGo answered HTTP 500"


def test_offline(serve):
    def offline(request):
        raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")

    serve(offline)
    result = search(query="x")
    assert result.is_error
    assert result.output.startswith("could not reach DuckDuckGo: ")
