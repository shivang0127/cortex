"""Source-kind detection and the single `parse` entry point the pipeline calls."""

from pathlib import PurePosixPath, PureWindowsPath

from secondbrain.pipeline.parse.base import FetchError, ParsedDocument, ParseError
from secondbrain.pipeline.parse.docx import parse_docx
from secondbrain.pipeline.parse.markdown import decode_text_bytes, parse_markdown, parse_plain_text
from secondbrain.pipeline.parse.pdf import parse_pdf
from secondbrain.pipeline.parse.web import parse_web, validate_http_url
from secondbrain.pipeline.parse.youtube import is_youtube_url, parse_youtube

__all__ = [
    "FetchError",
    "ParseError",
    "ParsedDocument",
    "detect_file_kind",
    "detect_url_kind",
    "parse_file",
    "parse_url",
    "stem_of",
]

FILE_KINDS_BY_EXTENSION = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".text": "text",
    ".docx": "docx",
}
SUPPORTED_EXTENSIONS = tuple(sorted(FILE_KINDS_BY_EXTENSION))


def _basename(filename: str) -> str:
    # Browsers may send a full path on some platforms; keep only the leaf.
    return PureWindowsPath(PurePosixPath(filename).name).name


def stem_of(filename: str) -> str:
    return PurePosixPath(_basename(filename)).stem or _basename(filename)


def detect_file_kind(filename: str, head: bytes) -> str:
    """Kind from the extension, cross-checked against magic bytes for binaries."""
    suffix = PurePosixPath(_basename(filename)).suffix.lower()
    kind = FILE_KINDS_BY_EXTENSION.get(suffix)
    if kind is None:
        raise ParseError(
            f"unsupported file type {suffix or '(none)'}; supported: "
            + ", ".join(SUPPORTED_EXTENSIONS)
        )
    if kind == "pdf" and not head.startswith(b"%PDF"):
        raise ParseError("file has a .pdf extension but is not a PDF")
    if kind == "docx" and not head.startswith(b"PK"):
        raise ParseError("file has a .docx extension but is not a DOCX (zip) package")
    if kind in ("markdown", "text") and b"\x00" in head:
        raise ParseError("file looks binary, not text")
    return kind


def detect_url_kind(url: str) -> str:
    validate_http_url(url)
    return "youtube" if is_youtube_url(url) else "web"


def parse_file(kind: str, data: bytes, *, filename: str) -> ParsedDocument:
    fallback = stem_of(filename)
    if kind == "pdf":
        return parse_pdf(data, fallback_title=fallback)
    if kind == "docx":
        return parse_docx(data, fallback_title=fallback)
    if kind == "markdown":
        return parse_markdown(decode_text_bytes(data), fallback_title=fallback)
    if kind == "text":
        return parse_plain_text(decode_text_bytes(data), fallback_title=fallback)
    raise ParseError(f"no file parser for kind {kind!r}")


def parse_url(kind: str, url: str) -> ParsedDocument:
    if kind == "web":
        return parse_web(url)
    if kind == "youtube":
        return parse_youtube(url)
    raise ParseError(f"no URL parser for kind {kind!r}")
