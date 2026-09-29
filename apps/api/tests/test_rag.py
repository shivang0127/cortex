"""The RAG pipeline's own logic: context assembly, the relevance floor, and the
citation contract. Unit tests — no database, no model."""

import uuid

import pytest

from secondbrain.config import Settings
from secondbrain.db.models import Chunk, Document
from secondbrain.services import rag
from secondbrain.services.rag import (
    INSUFFICIENT,
    REFUSAL_TEXT,
    Source,
    _clears_floor,
    _finish,
    _truncate,
    build_messages,
    prompt_hash,
    resolve_citations,
)
from secondbrain.services.search import SearchHit


def make_chunk(text: str, *, headings: list[str] | None = None, page: int | None = None) -> Chunk:
    return Chunk(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        ordinal=1,
        text=text,
        heading_path=headings or [],
        page_start=page,
        page_end=page,
        char_start=0,
        char_end=len(text),
        token_count=len(text.split()),
    )


def make_source(marker: int, text: str = "body text", **kwargs) -> Source:
    chunk = make_chunk(text, **kwargs)
    document = Document(id=chunk.document_id, kind="markdown", title="Doc", content_hash="x")
    hit = SearchHit(chunk=chunk, document=document, score=0.5)
    return Source(marker=marker, hit=hit, text=text)


# ── the relevance floor ───────────────────────────────────────────────────


def test_floor_accepts_confident_vectors_and_any_keyword_hit() -> None:
    chunk, doc = make_chunk("t"), Document(kind="markdown", title="d", content_hash="y")
    strong = SearchHit(chunk=chunk, document=doc, score=1, similarity=0.80, semantic_rank=1)
    weak = SearchHit(chunk=chunk, document=doc, score=1, similarity=0.20, semantic_rank=9)
    lexical = SearchHit(chunk=chunk, document=doc, score=1, keyword_rank=1, keyword_score=0.4)

    assert _clears_floor(strong, 0.45)
    assert not _clears_floor(weak, 0.45), "a distant vector is not evidence"
    assert _clears_floor(lexical, 0.45), "an exact term match counts even without a vector"
    assert _clears_floor(weak, 0.10), "the floor is configuration, not a constant"


# ── truncation ────────────────────────────────────────────────────────────


def test_truncate_leaves_short_text_alone() -> None:
    assert _truncate("a short passage", 100) == ("a short passage", False)


def test_truncate_cuts_on_a_boundary_never_mid_word() -> None:
    text = "\n\n".join(f"Paragraph {i} with several words in it." for i in range(40))
    cut, truncated = _truncate(text, 40)
    assert truncated and len(cut) < len(text)
    assert text.startswith(cut), "a prefix of the original"
    assert not cut.endswith(" ") and cut == cut.rstrip()
    assert cut[-1] in ".!?" or text[len(cut)] in " \n", "landed on a boundary"


# ── context assembly ──────────────────────────────────────────────────────


def test_source_header_carries_the_location() -> None:
    source = make_source(3, "text", headings=["Ch 3", "Attention"], page=7)
    header = source.header()
    assert header.startswith("[S3] ")
    assert '"Doc"' in header and "Ch 3 › Attention" in header and "p. 7" in header
    assert source.render().startswith(header)


def test_rendered_sources_are_numbered_from_one(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = [make_source(i) for i in (1, 2, 3)]
    messages = build_messages("why?", sources, "v1")
    system = messages[0].content
    assert [m.role for m in messages] == ["system", "user"]
    assert messages[1].content == "why?"
    for marker in ("[S1]", "[S2]", "[S3]"):
        assert marker in system
    assert INSUFFICIENT in system, "the refusal contract is in the prompt"
    assert "only from the user's own" in system.lower() or "only" in system.lower()


def test_prompt_hash_is_stable_and_sensitive() -> None:
    a = build_messages("q", [make_source(1)], "v1")
    b = build_messages("q", [make_source(1)], "v1")
    c = build_messages("different", [make_source(1)], "v1")
    assert prompt_hash(a) == prompt_hash(b) and len(prompt_hash(a)) == 64
    assert prompt_hash(a) != prompt_hash(c)


# ── citation validation: the core Phase 3 contract ───────────────────────


def test_valid_markers_are_kept_and_resolved() -> None:
    sources = [make_source(1), make_source(2)]
    text, citations = resolve_citations("A [S1]. B [S2]. C [S1].", sources)
    assert text == "A [S1]. B [S2]. C [S1]."
    assert [c.marker for c in citations] == [1, 2], "deduplicated, in marker order"
    assert citations[0].source is sources[0]


def test_invented_markers_are_removed_entirely() -> None:
    text, citations = resolve_citations(
        "Real [S1]. Invented [S9]. Also fake [S42].", [make_source(1)]
    )
    assert "[S9]" not in text and "[S42]" not in text
    assert "[S1]" in text
    assert [c.marker for c in citations] == [1]
    assert text == "Real [S1]. Invented. Also fake."


def test_answer_with_no_markers_yields_no_citations() -> None:
    text, citations = resolve_citations("A confident, uncited claim.", [make_source(1)])
    assert citations == [] and text == "A confident, uncited claim."


def test_finish_classifies_refusal_grounded_and_ungrounded() -> None:
    sources = [make_source(1)]

    text, citations, refused, grounded = _finish("Answer [S1].", sources)
    assert (refused, grounded) == (False, True) and citations

    text, citations, refused, grounded = _finish(
        f"{INSUFFICIENT}\nNothing covers gradient descent.", sources
    )
    assert refused and not grounded and citations == []
    assert text.startswith(REFUSAL_TEXT) and "gradient descent" in text

    text, citations, refused, grounded = _finish("Unsupported claim.", sources)
    assert (refused, grounded) == (False, False), "no valid citation ⇒ not grounded"

    _, _, _, grounded = _finish("Only an invention [S7].", sources)
    assert not grounded, "an invented citation does not make an answer grounded"


def test_refusal_detection_is_case_and_padding_tolerant() -> None:
    for raw in (
        INSUFFICIENT,
        f"  {INSUFFICIENT}  ",
        INSUFFICIENT.lower(),
        f"{INSUFFICIENT}: no source",
    ):
        _, _, refused, _ = _finish(raw, [make_source(1)])
        assert refused, raw


# ── budgeting ─────────────────────────────────────────────────────────────


def test_build_sources_respects_the_token_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tiny budget keeps the best hits and drops the rest — whole blocks only."""
    settings = Settings(
        rag_context_tokens=120, rag_top_k=8, rag_max_source_tokens=60, rag_expand_to_parents=False
    )
    hits = []
    for i in range(6):
        chunk = make_chunk(" ".join(f"word{i}-{j}" for j in range(40)))
        doc = Document(id=chunk.document_id, kind="markdown", title=f"Doc {i}", content_hash=str(i))
        hits.append(SearchHit(chunk=chunk, document=doc, score=1.0 - i / 10, similarity=0.9))

    sources, spent = rag.build_sources(None, hits, settings)  # session unused without parents
    assert 0 < len(sources) < 6, "the budget bound the list"
    assert spent <= settings.rag_context_tokens
    assert [s.marker for s in sources] == list(range(1, len(sources) + 1))
    assert sources[0].hit is hits[0], "best-ranked first"


def test_top_k_caps_the_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(rag_context_tokens=10_000, rag_top_k=3, rag_expand_to_parents=False)
    hits = []
    for i in range(6):
        chunk = make_chunk("short text")
        doc = Document(id=chunk.document_id, kind="markdown", title=f"D{i}", content_hash=str(i))
        hits.append(SearchHit(chunk=chunk, document=doc, score=1.0, similarity=0.9))
    sources, _ = rag.build_sources(None, hits, settings)
    assert len(sources) == 3


def test_prompt_never_mentions_a_marker_that_may_not_exist() -> None:
    """A worked example containing [S2] is a phantom source when only one was
    retrieved: the model copies it and the validator has to strip it. Only [S1] is
    guaranteed to exist, because generation never runs with zero sources."""
    import re

    from secondbrain.prompts import load_prompt

    template = load_prompt("answer")
    scaffold = template.replace("{sources}", "")
    markers = {int(n) for n in re.findall(r"\[S(\d+)\]", scaffold)}
    assert markers <= {1}, f"prompt names sources that may not exist: {sorted(markers - {1})}"
