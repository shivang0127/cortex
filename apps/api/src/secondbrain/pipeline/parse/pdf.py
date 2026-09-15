"""PDF parser on PyMuPDF (ARCHITECTURE.md §6 — "PyMuPDF for PDFs").

Headings are detected from typography: a line set noticeably larger than the
document's body size — or bold at body size — that is short and does not read
like a sentence. Levels come from the rank of distinct heading sizes. This is
a heuristic and is tuned by the constants below rather than hidden in code.

Scanned PDFs (no text layer) are rejected with a clear error instead of
producing an empty document; OCR is deliberately out of scope for now.
"""

import re
from collections import Counter
from dataclasses import dataclass

import pymupdf

from secondbrain.pipeline.parse.base import ParsedDocument, ParseError, TextBuilder

HEADING_SIZE_RATIO = 1.15  # a line ≥ 15% larger than body text is a heading candidate
MAX_HEADING_CHARS = 120
MAX_HEADING_LEVELS = 3
MIN_CHARS_PER_PAGE = 15  # below this average the PDF is treated as scanned / image-only
_BOLD_FLAG = 16
_SENTENCE_END = re.compile(r"[.!?:;,]$")
_HAS_LETTER = re.compile(r"[A-Za-z]")


@dataclass
class _Line:
    text: str
    size: float
    bold: bool


def _extract_lines(page: pymupdf.Page) -> list[list[_Line]]:
    """Text blocks → lines, each with its dominant font size and boldness."""
    blocks: list[list[_Line]] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:  # 0 = text, 1 = image
            continue
        lines: list[_Line] = []
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in line["spans"]).strip()
            # Dominant size: the size carrying the most characters on the line.
            by_size: Counter[float] = Counter()
            for s in spans:
                by_size[round(s["size"] * 2) / 2] += len(s["text"])
            size = by_size.most_common(1)[0][0]
            bold = all(s["flags"] & _BOLD_FLAG for s in spans)
            lines.append(_Line(text, size, bold))
        if lines:
            blocks.append(lines)
    return blocks


def _body_size(pages: list[list[list[_Line]]]) -> float:
    weights: Counter[float] = Counter()
    for blocks in pages:
        for block in blocks:
            for line in block:
                weights[line.size] += len(line.text)
    return weights.most_common(1)[0][0] if weights else 10.0


def _looks_like_heading(text: str) -> bool:
    return (
        0 < len(text) <= MAX_HEADING_CHARS
        and bool(_HAS_LETTER.search(text))
        and not _SENTENCE_END.search(text)
        and not text.startswith(("•", "-", "–"))
    )


def _join_lines(lines: list[_Line]) -> str:
    """Lines inside a block are layout, not paragraphs: join them, undoing hyphenation."""
    out = ""
    for line in lines:
        if out.endswith("-") and line.text[:1].islower():
            out = out[:-1] + line.text
        elif out:
            out += " " + line.text
        else:
            out = line.text
    return out


def parse_pdf(data: bytes, *, fallback_title: str | None = None) -> ParsedDocument:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises a range of error types for bad input
        raise ParseError(f"not a readable PDF: {exc}") from exc
    if doc.is_encrypted and not doc.authenticate(""):
        raise ParseError("PDF is password-protected")

    pages = [_extract_lines(page) for page in doc]
    total_chars = sum(len(ln.text) for blocks in pages for block in blocks for ln in block)
    if doc.page_count and total_chars / doc.page_count < MIN_CHARS_PER_PAGE:
        raise ParseError(
            "PDF has no usable text layer (scanned or image-only?) — OCR is not supported yet"
        )

    body = _body_size(pages)
    heading_sizes = sorted(
        {
            ln.size
            for blocks in pages
            for block in blocks
            for ln in block
            if ln.size >= body * HEADING_SIZE_RATIO and _looks_like_heading(ln.text)
        },
        reverse=True,
    )[:MAX_HEADING_LEVELS]
    level_of = {size: i + 1 for i, size in enumerate(heading_sizes)}
    bold_level = min(len(heading_sizes) + 1, MAX_HEADING_LEVELS)

    builder = TextBuilder()
    for blocks in pages:
        builder.mark_page()
        for block in blocks:
            single = len(block) == 1
            first = block[0]
            if single and first.size in level_of and _looks_like_heading(first.text):
                builder.add_heading(level_of[first.size], first.text)
            elif (
                single
                and first.bold
                and first.size >= body
                and _looks_like_heading(first.text)
                and len(first.text.split()) <= 12
            ):
                builder.add_heading(bold_level, first.text)
            else:
                builder.add_paragraph(_join_lines(block))

    text = builder.build()
    meta_title = (doc.metadata or {}).get("title", "").strip()
    first_h1 = next((h.text for h in builder.headings if h.level == 1), None)
    title = meta_title or first_h1 or fallback_title
    meta = {
        "pages": doc.page_count,
        "pdf_metadata": {k: v for k, v in (doc.metadata or {}).items() if v},
        "body_font_size": body,
    }
    return ParsedDocument(
        title=title,
        text=text,
        headings=builder.headings,
        page_offsets=builder.page_offsets,
        meta=meta,
    )
