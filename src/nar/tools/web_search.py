"""The ``web_search`` tool: search the web through DuckDuckGo, without an API key."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from nar.tools import web
from nar.tools.base import Tool, ToolContext, ToolResult

SEARCH_URL = "https://html.duckduckgo.com/html/"
DEFAULT_RESULTS = 5
MAX_RESULTS = 10
REFUSED = "DuckDuckGo refused the search (probably rate limiting); try again later"


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web with DuckDuckGo and return numbered results with title, URL and "
        "snippet. Use it to find documentation, error explanations or current information; "
        "then read a page with web_fetch."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "description": "Search terms."},
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_RESULTS,
                "description": f"How many results to return (default {DEFAULT_RESULTS}).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    requires_approval = False

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        query = arguments["query"]
        limit = arguments.get("max_results", DEFAULT_RESULTS)
        try:
            with web.http_client() as client:
                response = client.post(SEARCH_URL, data={"q": query})
        except httpx.HTTPError as exc:
            message = web.request_error_message(SEARCH_URL, exc)
            return ToolResult.error(message.replace(SEARCH_URL, "DuckDuckGo"))
        if response.status_code in (202, 403, 429):
            return ToolResult.error(REFUSED)
        if response.status_code != 200:
            return ToolResult.error(f"DuckDuckGo answered HTTP {response.status_code}")

        results = parse_results(response.text)
        if not results and _is_challenge(response.text):
            return ToolResult.error(REFUSED)
        results = results[:limit]
        if not results:
            return ToolResult.ok(f"(no results for {query})", summary="0 results")
        blocks = [
            f"{number}. {result.title}\n   {result.url}\n   {result.snippet}".rstrip()
            for number, result in enumerate(results, start=1)
        ]
        return ToolResult.ok("\n\n".join(blocks), summary=f"{len(results)} results")


def parse_results(html: str) -> list[SearchResult]:
    """Organic results of a DuckDuckGo HTML results page, in order."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for block in soup.select("div.result"):
        if "result--ad" in (block.get("class") or []):
            continue
        link = block.select_one("a.result__a")
        if link is None or not link.get("href"):
            continue
        snippet = block.select_one(".result__snippet")
        results.append(
            SearchResult(
                title=_clean(link.get_text()),
                url=_destination(str(link["href"])),
                snippet=_clean(snippet.get_text()) if snippet else "",
            )
        )
    return results


def _destination(href: str) -> str:
    """Undo DuckDuckGo's redirect links (``//duckduckgo.com/l/?uddg=<url>``)."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return target[0]
    return href


def _is_challenge(html: str) -> bool:
    lowered = html.lower()
    return "anomaly" in lowered or "challenge" in lowered


def _clean(text: str) -> str:
    return " ".join(text.split())
