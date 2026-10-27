import httpx

from nar import __version__
from nar.tools import web


def test_client_sends_user_agent_and_follows_redirects():
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["user-agent"]))
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://example.test/new"})
        return httpx.Response(200, text="ok")

    with web.http_client(httpx.MockTransport(handler)) as client:
        response = client.get("https://example.test/old")
    assert response.text == "ok"
    assert [url for url, _ in seen] == ["https://example.test/old", "https://example.test/new"]
    assert seen[0][1] == f"nar/{__version__} (+https://github.com/rojb/nar)"


def test_error_messages():
    request = httpx.Request("GET", "https://example.test")
    url = "https://example.test"
    timeout = httpx.ReadTimeout("read timed out", request=request)
    assert web.request_error_message(url, timeout) == f"request to {url} timed out after 20 s"
    redirects = httpx.TooManyRedirects("loop", request=request)
    assert web.request_error_message(url, redirects) == f"too many redirects for {url}"
    refused = httpx.ConnectError("[Errno 111] Connection\nrefused", request=request)
    assert web.request_error_message(url, refused) == (
        f"could not reach {url}: [Errno 111] Connection refused"
    )
