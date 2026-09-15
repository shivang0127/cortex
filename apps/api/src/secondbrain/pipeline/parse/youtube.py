"""YouTube parser: the video's transcript, via youtube-transcript-api.

There is no audio processing here — only transcripts YouTube already has
(uploaded captions or auto-generated ones). Snippets are grouped into
time-windowed paragraphs so the chunker has natural boundaries, and each
paragraph's start time is recorded against its character offset so a chunk can
later be cited as "12:34 in the video". The title comes from YouTube's public
oEmbed endpoint, which needs no API key. Both network calls are injectable.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

from secondbrain.config import get_settings
from secondbrain.pipeline.parse.base import FetchError, ParsedDocument, ParseError, TextBuilder
from secondbrain.pipeline.parse.http import fetch

PARAGRAPH_SECONDS = 45.0  # start a new paragraph after this much video time
PARAGRAPH_MAX_CHARS = 900  # …or when the current paragraph grows past this

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True)
class Snippet:
    text: str
    start: float
    duration: float


@dataclass(frozen=True)
class VideoInfo:
    title: str | None
    author: str | None


TranscriptFetcher = Callable[[str], list[Snippet]]
InfoFetcher = Callable[[str], VideoInfo]


def extract_video_id(url: str) -> str | None:
    """Recognise watch, short, embed, live and youtu.be URLs. None if not YouTube."""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    candidate: str | None = None
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com", "youtube-nocookie.com"):
        parts = [p for p in parsed.path.split("/") if p]
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [None])[0]
        elif len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            candidate = parts[1]
    return candidate if candidate and _ID.match(candidate) else None


def is_youtube_url(url: str) -> bool:
    return extract_video_id(url) is not None


def fetch_transcript(video_id: str) -> list[Snippet]:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        CouldNotRetrieveTranscript,
        NoTranscriptFound,
        TranscriptsDisabled,
        VideoUnavailable,
    )

    settings = get_settings()
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=settings.youtube_languages)
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable) as exc:
        raise ParseError(f"no transcript available for video {video_id}: {exc}") from exc
    except CouldNotRetrieveTranscript as exc:
        raise FetchError(f"could not retrieve transcript for {video_id}: {exc}") from exc
    return [Snippet(s.text, float(s.start), float(s.duration)) for s in fetched]


def fetch_video_info(video_id: str) -> VideoInfo:
    """Title and channel from YouTube's public oEmbed endpoint (no API key needed)."""
    query = urlencode({"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"})
    try:
        response = fetch(f"https://www.youtube.com/oembed?{query}", accept="application/json")
        data = json.loads(response.text())
        return VideoInfo(title=data.get("title"), author=data.get("author_name"))
    except (ParseError, FetchError, ValueError):
        return VideoInfo(title=None, author=None)  # the title is nice-to-have, not required


def _format_time(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_youtube(
    url: str,
    *,
    fetch_transcript_fn: TranscriptFetcher | None = None,
    fetch_info_fn: InfoFetcher | None = None,
) -> ParsedDocument:
    video_id = extract_video_id(url)
    if video_id is None:
        raise ParseError(f"not a recognisable YouTube video URL: {url}")

    # Defaults resolved at call time so tests can swap the network functions out.
    snippets = (fetch_transcript_fn or fetch_transcript)(video_id)
    if not snippets:
        raise ParseError(f"transcript for video {video_id} is empty")
    info = (fetch_info_fn or fetch_video_info)(video_id)

    builder = TextBuilder()
    paragraph: list[str] = []
    paragraph_start = snippets[0].start
    chars = 0

    def flush() -> None:
        nonlocal paragraph, chars
        if paragraph:
            builder.mark_segment(paragraph_start)
            builder.add_paragraph(" ".join(paragraph))
            paragraph, chars = [], 0

    for snippet in snippets:
        clean = " ".join(snippet.text.split())
        if not clean:
            continue
        if paragraph and (
            snippet.start - paragraph_start >= PARAGRAPH_SECONDS
            or chars + len(clean) > PARAGRAPH_MAX_CHARS
        ):
            flush()
            paragraph_start = snippet.start
        paragraph.append(clean)
        chars += len(clean) + 1
    flush()

    text = builder.build()
    if not text.strip():
        raise ParseError(f"transcript for video {video_id} contains no text")

    last = snippets[-1]
    meta = {
        "video_id": video_id,
        "author": info.author,
        "duration_seconds": round(last.start + last.duration, 1),
        "duration": _format_time(last.start + last.duration),
        "snippet_count": len(snippets),
    }
    return ParsedDocument(
        title=info.title or f"YouTube video {video_id}",
        text=text,
        segment_offsets=builder.segment_offsets,
        meta={k: v for k, v in meta.items() if v is not None},
    )
