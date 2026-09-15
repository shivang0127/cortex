"""Parser unit tests: every source kind → the same ParsedDocument shape, with
offsets that point at the right places in the normalised text."""

import pytest
from tests.conftest import SAMPLE_MARKDOWN, make_docx, make_pdf

from secondbrain.pipeline import parse
from secondbrain.pipeline.parse import ParseError, detect_file_kind, detect_url_kind
from secondbrain.pipeline.parse.base import normalise_text
from secondbrain.pipeline.parse.docx import parse_docx
from secondbrain.pipeline.parse.markdown import decode_text_bytes, parse_markdown, parse_plain_text
from secondbrain.pipeline.parse.pdf import parse_pdf
from secondbrain.pipeline.parse.web import parse_web
from secondbrain.pipeline.parse.youtube import (
    Snippet,
    VideoInfo,
    extract_video_id,
    parse_youtube,
)


def _heading_texts(parsed) -> list[str]:
    return [h.text for h in parsed.headings]


def _assert_headings_anchor(parsed) -> None:
    """Every heading offset must point at the heading's own text."""
    for h in parsed.headings:
        assert parsed.text[h.offset :].lstrip("# ").startswith(h.text[:20]), h


# ── normalisation ─────────────────────────────────────────────────────────


def test_normalise_text_canonicalises_whitespace() -> None:
    assert normalise_text("a  b\r\n\r\n\r\n\r\nc \t d\n") == "a b\n\nc d\n"
    assert normalise_text("   \n  ") == ""


def test_decode_text_bytes_handles_bom_and_cp1252() -> None:
    assert decode_text_bytes("\ufeffhello".encode()) == "hello"
    assert decode_text_bytes(b"caf\xe9") == "café"


# ── markdown / text ───────────────────────────────────────────────────────


def test_markdown_headings_levels_and_offsets() -> None:
    parsed = parse_markdown(SAMPLE_MARKDOWN, fallback_title="notes")
    assert _heading_texts(parsed) == ["Automata", "Deterministic", "Grammars"]
    assert [h.level for h in parsed.headings] == [1, 2, 1]
    assert parsed.title == "Automata"
    _assert_headings_anchor(parsed)


def test_markdown_frontmatter_title_and_setext_and_fences() -> None:
    raw = (
        "---\ntitle: My Notes\ntags: [a, b]\n---\n"
        "Intro\n=====\n\ntext\n\nSub\n---\n\n```\n# not a heading\n```\n\n## Real\n\nbody\n"
    )
    parsed = parse_markdown(raw)
    assert parsed.title == "My Notes"
    assert parsed.meta["frontmatter"]["title"] == "My Notes"
    assert _heading_texts(parsed) == ["Intro", "Sub", "Real"]
    assert [h.level for h in parsed.headings] == [1, 2, 2]
    assert "title: My Notes" not in parsed.text


def test_plain_text_has_no_structure() -> None:
    parsed = parse_plain_text("one\r\n\r\ntwo", fallback_title="file")
    assert parsed.text == "one\n\ntwo\n"
    assert parsed.headings == []
    assert parsed.title == "file"


# ── pdf ───────────────────────────────────────────────────────────────────


def test_pdf_headings_pages_and_title() -> None:
    body = "This is body text that goes on for a while to establish the body size. " * 6
    data = make_pdf(
        [
            [
                ("Chapter One", 20, False),
                (body, 11, False),
                ("First Section", 15, False),
                (body, 11, False),
            ],
            [("Chapter Two", 20, False), (body, 11, False)],
        ]
    )
    parsed = parse_pdf(data, fallback_title="file")
    assert _heading_texts(parsed) == ["Chapter One", "First Section", "Chapter Two"]
    assert [h.level for h in parsed.headings] == [1, 2, 1]
    assert parsed.title == "Chapter One"
    assert parsed.meta["pages"] == 2
    assert len(parsed.page_offsets) == 2
    # The second chapter heading lives on page 2.
    assert parsed.headings[2].offset >= parsed.page_offsets[1]
    _assert_headings_anchor(parsed)


def test_pdf_without_text_layer_is_rejected() -> None:
    data = make_pdf([[]])  # one blank page
    with pytest.raises(ParseError, match="no usable text layer"):
        parse_pdf(data)


def test_pdf_garbage_is_rejected() -> None:
    with pytest.raises(ParseError, match="not a readable PDF"):
        parse_pdf(b"%PDF-1.7 this is not really a pdf")


# ── docx ──────────────────────────────────────────────────────────────────


def test_docx_styles_become_headings() -> None:
    data = make_docx(
        [
            ("Title", "Lecture Notes"),
            ("Heading 1", "Turing Machines"),
            ("Normal", "A Turing machine has a tape. " * 20),
            ("Heading 2", "Halting"),
            ("Normal", "The halting problem is undecidable."),
        ]
    )
    parsed = parse_docx(data)
    assert parsed.title == "Lecture Notes"
    assert _heading_texts(parsed) == ["Lecture Notes", "Turing Machines", "Halting"]
    assert [h.level for h in parsed.headings] == [1, 1, 2]
    assert "undecidable" in parsed.text
    _assert_headings_anchor(parsed)


def test_docx_garbage_is_rejected() -> None:
    with pytest.raises(ParseError):
        parse_docx(b"PK\x03\x04 not a docx")


# ── kind detection ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("filename", "head", "kind"),
    [
        ("a.pdf", b"%PDF-1.4", "pdf"),
        ("notes.MD", b"# hi", "markdown"),
        ("x.txt", b"plain", "text"),
        ("essay.docx", b"PK\x03\x04", "docx"),
        ("C:\\Users\\me\\Downloads\\paper.pdf", b"%PDF", "pdf"),
    ],
)
def test_detect_file_kind(filename: str, head: bytes, kind: str) -> None:
    assert detect_file_kind(filename, head) == kind


@pytest.mark.parametrize(
    ("filename", "head"),
    [
        ("archive.zip", b"PK"),
        ("code.py", b"print(1)"),
        ("noext", b"text"),
        ("fake.pdf", b"not a pdf"),
        ("fake.docx", b"not zip"),
        ("binary.txt", b"\x00\x01\x02"),
    ],
)
def test_detect_file_kind_rejects(filename: str, head: bytes) -> None:
    with pytest.raises(ParseError):
        detect_file_kind(filename, head)


def test_detect_url_kind() -> None:
    assert detect_url_kind("https://example.com/article") == "web"
    assert detect_url_kind("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"
    assert detect_url_kind("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    with pytest.raises(ValueError):
        detect_url_kind("ftp://example.com/x")
    with pytest.raises(ValueError):
        detect_url_kind("not a url")


# ── web ───────────────────────────────────────────────────────────────────

ARTICLE_HTML = """<html><head><title>Optimisers Explained</title>
<meta name="author" content="Jane Doe"></head><body>
<nav><a href="/">Home</a></nav>
<article><h1>Optimisers Explained</h1><p>{p}</p><h2>Momentum</h2><p>{p}</p></article>
<footer>Footer junk</footer></body></html>""".format(
    p=" ".join(f"Sentence {i} discusses gradient descent and learning rates." for i in range(40))
)


def test_web_parser_extracts_article_with_headings() -> None:
    parsed = parse_web("https://example.com/post", fetch=lambda url: ARTICLE_HTML)
    assert parsed.title == "Optimisers Explained"
    assert parsed.meta["author"] == "Jane Doe"
    assert "Momentum" in _heading_texts(parsed)
    assert "Footer junk" not in parsed.text
    assert "Home" not in parsed.text
    _assert_headings_anchor(parsed)


def test_web_parser_rejects_empty_page() -> None:
    with pytest.raises(ParseError, match="no readable article"):
        parse_web("https://example.com/empty", fetch=lambda url: "<html><body></body></html>")


# ── youtube ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    ],
)
def test_extract_video_id(url: str) -> None:
    assert extract_video_id(url) == "dQw4w9WgXcQ"


def test_extract_video_id_rejects_non_youtube() -> None:
    assert extract_video_id("https://vimeo.com/12345") is None
    assert extract_video_id("https://www.youtube.com/") is None


def test_youtube_parser_groups_snippets_into_timed_paragraphs() -> None:
    snippets = [
        Snippet(f"snippet {i} of the talk", start=i * 10.0, duration=10.0) for i in range(12)
    ]
    parsed = parse_youtube(
        "https://youtu.be/dQw4w9WgXcQ",
        fetch_transcript_fn=lambda vid: snippets,
        fetch_info_fn=lambda vid: VideoInfo(title="A Talk", author="Someone"),
    )
    assert parsed.title == "A Talk"
    assert parsed.meta["video_id"] == "dQw4w9WgXcQ"
    assert parsed.meta["duration"] == "2:00"
    # 120 s of speech at 45 s per paragraph → 3 paragraphs, each anchored to a start time.
    assert [s for _, s in parsed.segment_offsets] == [0.0, 50.0, 100.0]
    for offset, _ in parsed.segment_offsets:
        assert parsed.text[offset:].startswith("snippet")


def test_youtube_parser_without_transcript() -> None:
    def no_transcript(vid: str) -> list[Snippet]:
        raise ParseError("no transcript")

    with pytest.raises(ParseError):
        parse_youtube(
            "https://youtu.be/dQw4w9WgXcQ",
            fetch_transcript_fn=no_transcript,
            fetch_info_fn=lambda vid: VideoInfo(None, None),
        )


def test_parse_file_dispatch() -> None:
    assert parse.parse_file("text", b"hello", filename="a.txt").text == "hello\n"
    with pytest.raises(ParseError):
        parse.parse_file("bogus", b"", filename="a")


def test_web_parser_strips_footnote_markup_and_junk_author() -> None:
    html = ARTICLE_HTML.replace(
        '<meta name="author" content="Jane Doe">',
        '<meta name="author" content="Authority control databases National Czech Republic">',
    ).replace(
        "<h2>Momentum</h2>", "<h2>Momentum</h2><p>Cited claim.<sup>[1]</sup> Next sentence.</p>"
    )
    parsed = parse_web("https://example.com/post", fetch=lambda url: html)
    assert "<sup>" not in parsed.text and "[1]" not in parsed.text
    assert "Cited claim. Next sentence." in parsed.text
    assert "author" not in parsed.meta
