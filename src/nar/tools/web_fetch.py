"""The ``web_fetch`` tool: download a web page and return it as Markdown or text."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify

from nar.tools import web
from nar.tools.base import Tool, ToolContext, ToolResult

MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
HTML_TYPES = ("text/html", "application/xhtml+xml")
TEXT_TYPES = ("application/json", "application/xml", "application/javascript")
# Page chrome that is noise for the model.
REMOVED_TAGS = (
    "head",
    "title",  # extracted first; it can also appear outside <head>
    "script",
    "style",
    "noscript",
    "nav",
    "footer",
    "aside",
    "form",
    "iframe",
    "svg",
    "template",
)


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Download a web page (http or https) and return its title, final URL and content. "
        "HTML is converted to Markdown with absolute links (menus, scripts and footers "
        "removed); text, JSON and XML are returned as they are. Images, PDFs and other "
        "binary content are not supported. Long pages are truncated."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The http or https URL to fetch."},
        },
        "required": ["url"],
        "additionalProperties": False,
    }
    requires_approval = False

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        url = arguments["url"].strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ToolResult.error(f"only http and https URLs are supported: {url}")

        try:
            with web.http_client() as client, client.stream("GET", url) as response:
                if response.status_code >= 400:
                    return ToolResult.error(f"HTTP {response.status_code} for {response.url}")
                body, cut = _read_limited(response)
                content_type = _content_type(response)
                encoding = response.charset_encoding or "utf-8"
                final_url = str(response.url)
        except httpx.HTTPError as exc:
            return ToolResult.error(web.request_error_message(url, exc))

        kind = _kind(content_type, body)
        if kind is None:
            shown = content_type or "unknown"
            return ToolResult.error(f"content type {shown} is not supported: {final_url}")
        text = _decode(body, encoding)
        title = None
        if kind == "html":
            title, text = html_to_markdown(text, final_url)

        parts = [f"# {title}"] if title else []
        parts.append(f"URL: {final_url}")
        parts.extend(["", text.strip()])
        if cut:
            parts.append("[download stopped at 5 MB]")
        host = urlparse(final_url).netloc
        return ToolResult.ok("\n".join(parts), summary=f"fetched {len(text)} chars from {host}")


def html_to_markdown(html: str, base_url: str) -> tuple[str | None, str]:
    """Title and Markdown body of an HTML page, without page chrome, links made absolute."""
    soup = BeautifulSoup(html, "html.parser")
    title = " ".join(soup.title.get_text().split()) if soup.title else ""
    for tag in soup.find_all(REMOVED_TAGS):
        tag.decompose()
    for link in soup.find_all("a", href=True):
        link["href"] = urljoin(base_url, str(link["href"]))
    for image in soup.find_all("img", src=True):
        image["src"] = urljoin(base_url, str(image["src"]))
    markdown = markdownify(str(soup), heading_style="ATX", bullets="-")
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    return title or None, markdown


def _read_limited(response: httpx.Response) -> tuple[bytes, bool]:
    chunks = []
    size = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_DOWNLOAD_BYTES:
            return b"".join(chunks)[:MAX_DOWNLOAD_BYTES], True
    return b"".join(chunks), False


def _content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";")[0].strip().lower()


def _kind(content_type: str, body: bytes) -> str | None:
    """``"html"``, ``"text"`` or ``None`` when the content cannot be shown as text."""
    if content_type in HTML_TYPES:
        return "html"
    if (
        content_type.startswith("text/")
        or content_type in TEXT_TYPES
        or content_type.endswith(("+json", "+xml"))
    ):
        return "text"
    if content_type:
        return None
    head = body[:1024].lower()
    if b"<html" in head or b"<!doctype html" in head:
        return "html"
    try:
        body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return "text"


def _decode(body: bytes, encoding: str) -> str:
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:  # unknown charset name in the header
        return body.decode("utf-8", errors="replace")
