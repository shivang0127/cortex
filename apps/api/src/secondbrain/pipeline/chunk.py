"""Structure-aware chunking (ARCHITECTURE.md §6).

Two levels, both cut from `documents.raw_text` by character offset:

* **Section chunks** (parents, `parent_ordinal is None`): one per heading-delimited
  section, carrying the full `heading_path`. Oversized sections are windowed on
  paragraph boundaries so a 40-page chapter with no sub-headings still yields
  readable parents.
* **Retrieval chunks** (children): ~`target_tokens` each, grown paragraph by
  paragraph inside their parent, with ~12 % overlap so a sentence cut at a
  boundary still appears whole somewhere.

The invariant every later phase relies on: `text == raw_text[char_start:char_end]`.
"""

import re
from bisect import bisect_right
from dataclasses import dataclass, field

from secondbrain.pipeline.parse.base import Heading, ParsedDocument, approx_token_count

_PARAGRAPH_BREAK = re.compile(r"\n\n+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int = 300  # retrieval chunk content size; long paragraphs are split to this
    max_tokens: int = 450  # hard cap on a retrieval chunk including its overlap
    overlap_ratio: float = 0.12  # share of target_tokens carried over from the previous chunk
    parent_max_tokens: int = 2000  # sections longer than this are windowed


@dataclass
class ChunkSpec:
    ordinal: int
    text: str
    heading_path: list[str]
    char_start: int
    char_end: int
    token_count: int
    page_start: int | None = None
    page_end: int | None = None
    parent_ordinal: int | None = None


@dataclass
class _Section:
    heading_path: list[str]
    start: int
    end: int
    pieces: list[tuple[int, int]] = field(default_factory=list)  # paragraph spans


def _sections(text: str, headings: list[Heading]) -> list[_Section]:
    """Split text at headings; each section knows its ancestor headings."""
    bounds = [h.offset for h in headings if 0 <= h.offset < len(text)]
    sections: list[_Section] = []
    stack: list[Heading] = []
    if not headings or headings[0].offset > 0:
        first_end = bounds[0] if bounds else len(text)
        sections.append(_Section([], 0, first_end))
    for i, heading in enumerate(headings):
        while stack and stack[-1].level >= heading.level:
            stack.pop()
        stack.append(heading)
        end = bounds[i + 1] if i + 1 < len(bounds) else len(text)
        sections.append(_Section([h.text for h in stack], heading.offset, end))
    return [s for s in sections if text[s.start : s.end].strip()]


def _paragraph_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Non-empty paragraph spans within [start, end), trimmed of surrounding whitespace."""
    spans: list[tuple[int, int]] = []
    pos = start
    for m in _PARAGRAPH_BREAK.finditer(text, start, end):
        spans.append((pos, m.start()))
        pos = m.end()
    spans.append((pos, end))
    trimmed = []
    for a, b in spans:
        piece = text[a:b]
        lead = len(piece) - len(piece.lstrip())
        trail = len(piece) - len(piece.rstrip())
        if b - trail > a + lead:
            trimmed.append((a + lead, b - trail))
    return trimmed


def _hard_split(text: str, start: int, end: int, max_tokens: int) -> list[tuple[int, int]]:
    """Cut one over-long sentence on whitespace so no atom exceeds max_tokens."""
    out: list[tuple[int, int]] = []
    a = start
    while approx_token_count(text[a:end]) > max_tokens:
        tokens = approx_token_count(text[a:end])
        limit = a + int((end - a) * max_tokens / tokens)
        cut = text.rfind(" ", a + 1, limit)
        if cut <= a:
            break
        out.append((a, cut))
        a = cut + 1
    out.append((a, end))
    return out


def _split_long(text: str, start: int, end: int, max_tokens: int) -> list[tuple[int, int]]:
    """Split one over-long paragraph into sentence groups of at most max_tokens."""
    if approx_token_count(text[start:end]) <= max_tokens:
        return [(start, end)]
    sentences: list[tuple[int, int]] = []
    pos = start
    for m in _SENTENCE_END.finditer(text, start, end):
        sentences.append((pos, m.start()))
        pos = m.end()
    sentences.append((pos, end))
    atoms = [
        piece
        for a, b in sentences
        if text[a:b].strip()
        for piece in _hard_split(text, a, b, max_tokens)
    ]
    return [(w[0][0], w[-1][1]) for w in _windows(atoms, text, max_tokens)]


def _windows(
    pieces: list[tuple[int, int]], text: str, max_tokens: int
) -> list[list[tuple[int, int]]]:
    """Group consecutive pieces into windows of at most max_tokens, each piece kept whole.

    The size check measures the combined span, not a sum of per-piece estimates, so
    rounding can never let a window creep past the cap.
    """
    windows: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    for a, b in pieces:
        if current and approx_token_count(text[current[0][0] : b]) > max_tokens:
            windows.append(current)
            current = []
        current.append((a, b))
    if current:
        windows.append(current)
    return windows


def _page_of(page_offsets: list[int], offset: int) -> int | None:
    if not page_offsets:
        return None
    return max(1, bisect_right(page_offsets, offset))


def chunk_document(parsed: ParsedDocument, config: ChunkingConfig | None = None) -> list[ChunkSpec]:
    cfg = config or ChunkingConfig()
    text = parsed.text
    if not text.strip():
        return []

    overlap_chars_target = int(cfg.overlap_ratio * cfg.target_tokens * 4)  # ≈4 chars/token
    chunks: list[ChunkSpec] = []
    ordinal = 0

    def make(a: int, b: int, path: list[str], parent: int | None) -> ChunkSpec:
        nonlocal ordinal
        spec = ChunkSpec(
            ordinal=ordinal,
            text=text[a:b],
            heading_path=list(path),
            char_start=a,
            char_end=b,
            token_count=approx_token_count(text[a:b]),
            page_start=_page_of(parsed.page_offsets, a),
            page_end=_page_of(parsed.page_offsets, max(a, b - 1)),
            parent_ordinal=parent,
        )
        ordinal += 1
        chunks.append(spec)
        return spec

    for section in _sections(text, parsed.headings):
        spans = _paragraph_spans(text, section.start, section.end)
        if section.heading_path and len(spans) >= 2:
            # The heading line is its own paragraph; glue it to the first real one so
            # no retrieval chunk is ever just "## Deterministic".
            spans[0:2] = [(spans[0][0], spans[1][1])]
        pieces: list[tuple[int, int]] = []
        for a, b in spans:
            pieces.extend(_split_long(text, a, b, cfg.target_tokens))
        if not pieces:
            continue

        for window in _windows(pieces, text, cfg.parent_max_tokens):
            parent = make(window[0][0], window[-1][1], section.heading_path, None)

            # Children: grow paragraph by paragraph up to target_tokens.
            groups = _windows(window, text, cfg.target_tokens)
            prev_end: int | None = None
            for group in groups:
                a, b = group[0][0], group[-1][1]
                if prev_end is not None and overlap_chars_target > 0:
                    # Extend backwards into the previous chunk, snapping to a word boundary,
                    # unless that would push the chunk over the hard size cap.
                    wanted = max(parent.char_start, a - overlap_chars_target)
                    snapped = text.find(" ", wanted, max(wanted, a - 1))  # first word boundary
                    if snapped != -1 and snapped + 1 < a:
                        extended = snapped + 1
                        if approx_token_count(text[extended:b]) <= cfg.max_tokens:
                            a = extended
                make(a, b, section.heading_path, parent.ordinal)
                prev_end = b

    return chunks
