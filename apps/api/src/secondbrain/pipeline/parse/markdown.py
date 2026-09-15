"""Markdown and plain-text parsers.

Markdown is already readable text, so the normalised text *is* the source
(whitespace-normalised); structure comes from ATX headings (`# …`) and
setext underlines. Fenced code blocks are skipped when detecting headings so a
`# comment` inside a code fence is not mistaken for one.
"""

import re

from secondbrain.pipeline.parse.base import Heading, ParsedDocument, normalise_text

_ATX = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_SETEXT_H1 = re.compile(r"^=+\s*$")
_SETEXT_H2 = re.compile(r"^-+\s*$")
_FENCE = re.compile(r"^(```|~~~)")
_FRONTMATTER_DELIM = re.compile(r"^---\s*$")


def find_markdown_headings(text: str) -> list[Heading]:
    headings: list[Heading] = []
    lines = text.split("\n")
    offset = 0
    in_fence = False
    prev_line, prev_offset = "", 0
    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            if m := _ATX.match(line):
                headings.append(Heading(len(m.group(1)), m.group(2).strip(), offset))
            elif prev_line.strip() and not _ATX.match(prev_line):
                if _SETEXT_H1.match(line):
                    headings.append(Heading(1, prev_line.strip(), prev_offset))
                elif _SETEXT_H2.match(line) and len(line.strip()) >= 3:
                    headings.append(Heading(2, prev_line.strip(), prev_offset))
        prev_line, prev_offset = line, offset
        offset += len(line) + 1
    return headings


def _strip_frontmatter(text: str) -> tuple[str, dict[str, str]]:
    """Remove a leading YAML front-matter block; keep simple `key: value` pairs as meta."""
    lines = text.split("\n")
    if not lines or not _FRONTMATTER_DELIM.match(lines[0]):
        return text, {}
    for i in range(1, min(len(lines), 100)):
        if _FRONTMATTER_DELIM.match(lines[i]):
            meta: dict[str, str] = {}
            for raw in lines[1:i]:
                key, sep, value = raw.partition(":")
                if sep and key.strip() and not key.startswith((" ", "-")):
                    meta[key.strip()] = value.strip().strip("\"'")
            return "\n".join(lines[i + 1 :]), meta
    return text, {}


def parse_markdown(raw: str, *, fallback_title: str | None = None) -> ParsedDocument:
    body, frontmatter = _strip_frontmatter(raw)
    text = normalise_text(body)
    headings = find_markdown_headings(text)
    title = frontmatter.get("title") or (headings[0].text if headings else None) or fallback_title
    meta: dict[str, object] = {}
    if frontmatter:
        meta["frontmatter"] = frontmatter
    return ParsedDocument(title=title, text=text, headings=headings, meta=meta)


def parse_plain_text(raw: str, *, fallback_title: str | None = None) -> ParsedDocument:
    text = normalise_text(raw)
    return ParsedDocument(title=fallback_title, text=text)


def decode_text_bytes(data: bytes) -> str:
    """UTF-8 (with or without BOM) first; fall back to cp1252 rather than failing."""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", errors="replace")
