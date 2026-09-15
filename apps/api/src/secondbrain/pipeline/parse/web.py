"""Web-page parser: fetch HTML, extract the article with trafilatura.

trafilatura's Markdown output keeps headings as `#` lines, so the Markdown
heading detector is reused and web pages get real `heading_path`s. The fetch
is injectable so tests never touch the network.
"""

import re
from collections.abc import Callable
from urllib.parse import urlparse

import trafilatura

from secondbrain.pipeline.parse.base import ParsedDocument, ParseError, normalise_text
from secondbrain.pipeline.parse.http import fetch
from secondbrain.pipeline.parse.markdown import find_markdown_headings

Fetcher = Callable[[str], str]

MAX_AUTHOR_CHARS = 60
MAX_AUTHOR_WORDS = 5  # "Authority control databases National Czech Republic" is not a byline
_FOOTNOTE_REF = re.compile(r"<sup>.*?</sup>", re.DOTALL)  # Wikipedia-style [1] markers
_RESIDUAL_TAG = re.compile(r"</?(?:sup|sub|span|div|a|br)\b[^>]*>")


def _plausible_byline(author: str) -> bool:
    return 0 < len(author) <= MAX_AUTHOR_CHARS and len(author.split()) <= MAX_AUTHOR_WORDS


def _strip_residual_html(markdown: str) -> str:
    """trafilatura's Markdown keeps a few inline HTML tags; drop the noise they carry."""
    return _RESIDUAL_TAG.sub("", _FOOTNOTE_REF.sub("", markdown))


def validate_http_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL must start with http:// or https://")
    return parsed.geturl()


def fetch_html(url: str) -> str:
    response = fetch(url)
    content_type = response.content_type.lower()
    if content_type and "html" not in content_type and "xml" not in content_type:
        raise ParseError(f"not an HTML page (content-type: {content_type})")
    return response.text()


def parse_web(url: str, *, fetch: Fetcher | None = None) -> ParsedDocument:
    html = (fetch or fetch_html)(url)  # resolved at call time so tests can swap it
    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_comments=False,
    )
    if not (markdown or "").strip():
        raise ParseError("no readable article content found on the page")
    metadata = trafilatura.extract_metadata(html, default_url=url)

    text = normalise_text(_strip_residual_html(markdown))
    headings = find_markdown_headings(text)
    author = (metadata.author or "").strip()
    meta: dict[str, object] = {
        k: v
        for k, v in {
            # trafilatura's author heuristic sometimes returns navigation text; a real
            # byline is short.
            "author": author if _plausible_byline(author) else None,
            "published_at": metadata.date,
            "site_name": metadata.sitename,
            "description": metadata.description,
        }.items()
        if v
    }
    title = (metadata.title or "").strip() or (headings[0].text if headings else None) or url
    return ParsedDocument(title=title, text=text, headings=headings, meta=meta)
