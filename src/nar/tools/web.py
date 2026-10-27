"""HTTP settings and error messages shared by the web tools.

Tools call :func:`http_client` through this module so tests can replace it
with a client on ``httpx.MockTransport``.
"""

from __future__ import annotations

import httpx

from nar import __version__

USER_AGENT = f"nar/{__version__} (+https://github.com/rojb/nar)"
TIMEOUT_SECONDS = 20.0
MAX_REDIRECTS = 5


def http_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """A client with nar's User-Agent, timeout and redirect policy (``transport`` for tests)."""
    return httpx.Client(
        transport=transport,
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        headers={"User-Agent": USER_AGENT},
    )


def request_error_message(url: str, exc: httpx.HTTPError) -> str:
    """One line for the model about a request that got no usable answer."""
    if isinstance(exc, httpx.TimeoutException):
        return f"request to {url} timed out after {TIMEOUT_SECONDS:g} s"
    if isinstance(exc, httpx.TooManyRedirects):
        return f"too many redirects for {url}"
    reason = " ".join(str(exc).split()) or type(exc).__name__
    return f"could not reach {url}: {reason}"
