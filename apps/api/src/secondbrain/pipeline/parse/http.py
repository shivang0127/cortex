"""Minimal HTTP fetching for URL sources, on the standard library.

Why not httpx: it fixes the header order on the wire (User-Agent last), which
some sites' bot filters — Wikimedia's edge among them — reject outright, while
the same request from `urllib` is accepted. For a handful of page fetches the
standard library is enough, and one fewer runtime dependency.

Errors are split into the two classes the worker cares about: a `ParseError`
is permanent (4xx, wrong content type, too large), a `FetchError` is worth a
retry (network trouble, 5xx).
"""

import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message

from secondbrain.config import get_settings
from secondbrain.pipeline.parse.base import FetchError, ParseError

DEFAULT_MAX_BYTES = 20 * 1024 * 1024
HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


@dataclass(frozen=True)
class Fetched:
    url: str  # final URL after redirects
    status: int
    content_type: str
    body: bytes

    def text(self) -> str:
        message = Message()
        message["content-type"] = self.content_type
        charset = message.get_param("charset") or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def fetch(url: str, *, accept: str = HTML_ACCEPT, max_bytes: int = DEFAULT_MAX_BYTES) -> Fetched:
    settings = get_settings()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": settings.http_user_agent,
            "Accept": accept,
            "Accept-Language": "en",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.http_timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            chunks: list[bytes] = []
            size = 0
            while True:
                part = response.read(64 * 1024)
                if not part:
                    break
                size += len(part)
                if size > max_bytes:
                    raise ParseError(f"response is larger than {max_bytes // (1024 * 1024)} MB")
                chunks.append(part)
            return Fetched(response.geturl(), response.status, content_type, b"".join(chunks))
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            raise ParseError(f"server returned HTTP {exc.code} for {url}") from exc
        raise FetchError(f"server returned HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise FetchError(f"could not fetch {url}: {exc}") from exc
