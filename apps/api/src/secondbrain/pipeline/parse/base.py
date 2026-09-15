"""The one shape every parser produces (ARCHITECTURE.md §6).

A parser turns a source — file bytes or a fetched URL — into *normalised text*
plus the structure the chunker needs: where the headings are, and (for paged
or timed media) where the pages or transcript segments begin. Everything
downstream is source-agnostic; only this module knows about PDFs, DOCX, HTML
or transcripts.

`text` is what gets stored in `documents.raw_text`. Every offset in this
object — headings, pages, segments — is a character index into that exact
string, and chunks will be cut from it by those indices. Parsers must never
return text that differs from what those offsets were computed against.
"""

import re
from dataclasses import dataclass, field
from typing import Any


class ParseError(Exception):
    """The source cannot be parsed. Not retryable: the bytes will not change."""


class FetchError(Exception):
    """A network fetch failed. Retryable: the site may be back in a minute."""


@dataclass(frozen=True)
class Heading:
    level: int  # 1 = top-level
    text: str
    offset: int  # index into ParsedDocument.text where the heading line starts


@dataclass
class ParsedDocument:
    title: str | None
    text: str
    headings: list[Heading] = field(default_factory=list)
    page_offsets: list[int] = field(default_factory=list)  # text index where page i starts (PDF)
    segment_offsets: list[tuple[int, float]] = field(default_factory=list)  # (index, seconds) (yt)
    meta: dict[str, Any] = field(default_factory=dict)


_WS_RUN = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def normalise_text(raw: str) -> str:
    """Canonical whitespace: LF newlines, single spaces, at most one blank line.

    Applied to every parser's output so offsets are computed once, on the final
    string, and so the same content from two sources hashes identically.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RUN.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip() + "\n" if text.strip() else ""


def approx_token_count(text: str) -> int:
    """Tokenizer-agnostic estimate: ~¾ of a word per token for English prose.

    The real tokenizer depends on the embedding/LLM model chosen in Phase 2; an
    estimate is enough to size chunks, and it keeps the L1 layer free of any
    model dependency.
    """
    words = len(text.split())
    return max(1, round(words * 1.33)) if words else 0


class TextBuilder:
    """Assembles normalised text paragraph by paragraph, recording structure as it goes.

    Parsers that reconstruct text from layout (PDF, DOCX, transcripts) use this
    instead of concatenating strings and normalising afterwards, because
    normalising *after* would shift every recorded offset.
    """

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._length = 0
        self.headings: list[Heading] = []
        self.page_offsets: list[int] = []
        self.segment_offsets: list[tuple[int, float]] = []

    @property
    def length(self) -> int:
        return self._length

    def _append(self, paragraph: str) -> int:
        """Append one normalised paragraph; return the offset where it starts."""
        clean = _WS_RUN.sub(" ", paragraph.replace("\r\n", "\n").replace("\r", "\n"))
        clean = "\n".join(line.strip() for line in clean.split("\n")).strip()
        clean = _BLANK_LINES.sub("\n\n", clean)
        if not clean:
            return self._length
        offset = self._length
        self._parts.append(clean + "\n\n")
        self._length += len(clean) + 2
        return offset

    def add_paragraph(self, paragraph: str) -> int:
        return self._append(paragraph)

    def add_heading(self, level: int, heading: str) -> int:
        clean = _WS_RUN.sub(" ", heading).strip()
        if not clean:
            return self._length
        offset = self._append(clean)
        self.headings.append(Heading(level, clean, offset))
        return offset

    def mark_page(self) -> None:
        self.page_offsets.append(self._length)

    def mark_segment(self, seconds: float) -> None:
        self.segment_offsets.append((self._length, seconds))

    def build(self) -> str:
        text = "".join(self._parts)
        # Trailing double newline → single, matching normalise_text's contract.
        return text[:-1] if text.endswith("\n\n") else text
