"""Chunker invariants: offsets are exact, structure is respected, sizes are bounded."""

from tests.conftest import SAMPLE_MARKDOWN

from secondbrain.pipeline.chunk import ChunkingConfig, ChunkSpec, chunk_document
from secondbrain.pipeline.parse.base import Heading, ParsedDocument, approx_token_count
from secondbrain.pipeline.parse.markdown import parse_markdown

SMALL = ChunkingConfig(target_tokens=60, max_tokens=90, overlap_ratio=0.12, parent_max_tokens=200)


def _check_invariants(parsed: ParsedDocument, chunks: list[ChunkSpec]) -> None:
    ordinals = [c.ordinal for c in chunks]
    assert ordinals == list(range(len(chunks))), "ordinals are dense and ordered"
    parents = {c.ordinal for c in chunks if c.parent_ordinal is None}
    for c in chunks:
        assert c.text == parsed.text[c.char_start : c.char_end], "offset invariant"
        assert c.text.strip(), "no empty chunks"
        assert c.token_count == approx_token_count(c.text)
        if c.parent_ordinal is not None:
            assert c.parent_ordinal in parents
            assert c.parent_ordinal < c.ordinal, "parent precedes its children"
            parent = chunks[c.parent_ordinal]
            assert parent.char_start <= c.char_start and c.char_end <= parent.char_end
            assert c.heading_path == parent.heading_path


def test_sections_follow_headings_and_paths_nest() -> None:
    parsed = parse_markdown(SAMPLE_MARKDOWN)
    chunks = chunk_document(parsed, SMALL)
    _check_invariants(parsed, chunks)
    paths = [c.heading_path for c in chunks if c.parent_ordinal is None]
    distinct = [p for i, p in enumerate(paths) if i == 0 or p != paths[i - 1]]
    assert distinct == [["Automata"], ["Automata", "Deterministic"], ["Grammars"]]
    # The first window of every section starts with its own heading line.
    previous: list[str] | None = None
    for c in chunks:
        if c.parent_ordinal is None:
            if c.heading_path != previous:
                assert c.text.startswith("#")
            previous = c.heading_path


def test_children_respect_target_and_overlap() -> None:
    parsed = parse_markdown(SAMPLE_MARKDOWN)
    chunks = chunk_document(parsed, SMALL)
    children = [c for c in chunks if c.parent_ordinal is not None]
    assert len(children) > 3, "long sections are split into several retrieval chunks"
    # A child only exceeds max_tokens when a single sentence does (none do here).
    assert all(c.token_count <= SMALL.max_tokens for c in children)
    # Consecutive children of one parent overlap: the next starts before the previous ends.
    by_parent: dict[int, list[ChunkSpec]] = {}
    for c in children:
        by_parent.setdefault(c.parent_ordinal, []).append(c)
    overlaps = [
        b.char_start < a.char_end
        for siblings in by_parent.values()
        for a, b in zip(siblings, siblings[1:], strict=False)
    ]
    assert overlaps and all(overlaps)


def test_preamble_before_first_heading_gets_empty_path() -> None:
    parsed = parse_markdown("Intro paragraph before any heading.\n\n# First\n\nBody.\n")
    chunks = chunk_document(parsed, SMALL)
    _check_invariants(parsed, chunks)
    assert chunks[0].heading_path == [] and chunks[0].text.startswith("Intro")
    assert chunks[2].heading_path == ["First"]


def test_long_paragraph_is_split_on_sentences() -> None:
    sentence = "This is a reasonably long sentence about automata theory and grammars. "
    parsed = parse_markdown("# Only\n\n" + sentence * 40 + "\n")
    chunks = chunk_document(parsed, SMALL)
    _check_invariants(parsed, chunks)
    children = [c for c in chunks if c.parent_ordinal is not None]
    assert len(children) >= 5
    assert children[0].text.startswith("# Only\n\nThis is"), "heading glued to first paragraph"
    for c in children:
        assert c.token_count <= SMALL.max_tokens
        assert c.text.strip().endswith("."), "splits land on sentence boundaries"


def test_oversized_section_is_windowed_into_several_parents() -> None:
    paragraphs = "\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(20))
    parsed = parse_markdown("# Huge\n\n" + paragraphs + "\n")
    chunks = chunk_document(parsed, SMALL)
    _check_invariants(parsed, chunks)
    parents = [c for c in chunks if c.parent_ordinal is None]
    assert len(parents) > 1
    assert all(p.heading_path == ["Huge"] for p in parents)
    assert all(p.token_count <= SMALL.parent_max_tokens for p in parents)


def test_page_numbers_come_from_page_offsets() -> None:
    text = "Page one text.\n\nStill page one.\n\nPage two text.\n\nPage three text.\n"
    parsed = ParsedDocument(
        title=None, text=text, page_offsets=[0, text.index("Page two"), text.index("Page three")]
    )
    chunks = chunk_document(parsed, ChunkingConfig(target_tokens=4, max_tokens=8))
    _check_invariants(parsed, chunks)
    children = [c for c in chunks if c.parent_ordinal is not None]
    assert children[0].page_start == 1
    assert children[-1].page_end == 3
    assert chunks[0].page_start == 1 and chunks[0].page_end == 3  # the single parent spans all


def test_headings_only_document_and_empty_document() -> None:
    assert chunk_document(ParsedDocument(title=None, text="   \n")) == []
    parsed = ParsedDocument(
        title=None, text="# A\n\n# B\n", headings=[Heading(1, "A", 0), Heading(1, "B", 5)]
    )
    chunks = chunk_document(parsed, SMALL)
    _check_invariants(parsed, chunks)
    assert [c.heading_path for c in chunks if c.parent_ordinal is None] == [["A"], ["B"]]
