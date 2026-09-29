"""Retrieval-augmented answering (ARCHITECTURE.md §5 "Query flow", §7).

    question
      → services.search.search(hybrid)      ← Phase 2, reused, never duplicated
      → relevance floor                     ← refuse here, before spending a model call
      → parent expansion + dedupe           ← "retrieve small, read big" (§6)
      → context budget, numbered [S1..Sn]
      → LLMProvider.complete/stream
      → citation parse + validation         ← the application owns the ID space
      → AnswerResult

The invariant that makes citations trustworthy: **the model never sees a chunk
id and never invents one.** It is shown small integers that this request
assigned, and anything it emits outside that set is discarded. A citation in
the response therefore always resolves to a row that was actually retrieved.

What this cannot do is verify that a sentence faithfully represents the chunk
it cites — that needs an entailment check and is deliberately out of scope.
`grounded` reports the weaker property that survived validation: the answer
carries at least one real citation.
"""

import hashlib
import logging
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from secondbrain.config import Settings
from secondbrain.db.models import Chunk, Document
from secondbrain.pipeline.parse.base import approx_token_count
from secondbrain.prompts import DEFAULT_VERSION, load_prompt
from secondbrain.providers.embedding import EmbeddingProvider
from secondbrain.providers.llm.base import GenerationConfig, LLMProvider, Message
from secondbrain.services.search import SearchFilters, SearchHit, search

log = logging.getLogger(__name__)

TASK = "rag.answer"
CITATION_MARKER = re.compile(r"\[S(\d+)\]")
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
REFUSAL_TEXT = (
    "Your Second Brain does not contain enough information to answer that. "
    "Import a source on this topic, or try Search Knowledge to see what is closest."
)


# ── Context ───────────────────────────────────────────────────────────────


@dataclass
class Source:
    """One numbered block of context, and the chunk it came from."""

    marker: int  # the S-number shown to the model
    hit: SearchHit
    text: str  # possibly a parent section, possibly truncated
    truncated: bool = False

    @property
    def chunk(self) -> Chunk:
        return self.hit.chunk

    @property
    def document(self) -> Document:
        return self.hit.document

    def header(self) -> str:
        parts = [f'"{self.document.title}"']
        if self.chunk.heading_path:
            parts.append(" › ".join(self.chunk.heading_path))
        if self.chunk.page_start:
            page = f"p. {self.chunk.page_start}"
            if self.chunk.page_end and self.chunk.page_end != self.chunk.page_start:
                page += f"–{self.chunk.page_end}"
            parts.append(page)
        return f"[S{self.marker}] " + " · ".join(parts)

    def render(self) -> str:
        suffix = "\n…(truncated)" if self.truncated else ""
        return f"{self.header()}\n{self.text}{suffix}"


@dataclass
class Citation:
    marker: int
    source: Source


@dataclass
class AnswerResult:
    question: str
    answer: str
    refused: bool
    grounded: bool
    sources: list[Source] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    retrieval_hits: int = 0
    sources_above_floor: int = 0
    context_tokens: int = 0
    truncated_output: bool = False
    model_id: str | None = None
    provider_id: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    prompt_version: str = DEFAULT_VERSION
    prompt_hash: str = ""
    messages: list[Message] = field(default_factory=list)  # for llm_calls payload logging


def _clears_floor(hit: SearchHit, min_similarity: float) -> bool:
    """A hit is evidence if the vector is confident *or* the words literally matched."""
    if hit.keyword_rank is not None:
        return True
    return hit.similarity is not None and hit.similarity >= min_similarity


def _parent_text(session: Session, chunk: Chunk) -> str | None:
    if chunk.parent_id is None:
        return None
    parent = session.get(Chunk, chunk.parent_id)
    return parent.text if parent is not None and parent.superseded_at is None else None


def _truncate(text: str, max_tokens: int) -> tuple[str, bool]:
    """Trim to a paragraph boundary (then a sentence, then a word). Never mid-word."""
    if approx_token_count(text) <= max_tokens:
        return text, False
    approx_chars = max(1, int(max_tokens * 4))
    window = text[:approx_chars]
    for boundary in ("\n\n", ". ", " "):
        cut = window.rfind(boundary)
        if cut > approx_chars // 2:
            return window[: cut + (1 if boundary == ". " else 0)].rstrip(), True
    return window.rstrip(), True


def build_sources(
    session: Session, hits: list[SearchHit], settings: Settings
) -> tuple[list[Source], int]:
    """Number the hits, expand to parents, and spend the token budget on whole blocks.

    Order is the fused ranking (best first) — the strongest evidence goes early,
    where models attend to it most reliably. Two hits inside one parent section
    collapse into a single source rather than repeating the passage.
    """
    sources: list[Source] = []
    used_parents: set[uuid.UUID] = set()
    budget = settings.rag_context_tokens
    spent = 0

    for hit in hits:
        chunk = hit.chunk
        text = chunk.text
        if settings.rag_expand_to_parents and chunk.parent_id is not None:
            if chunk.parent_id in used_parents:
                continue  # this passage is already inside a source we added
            if parent := _parent_text(session, chunk):
                text, used_parents = parent, used_parents | {chunk.parent_id}

        text, truncated = _truncate(text, settings.rag_max_source_tokens)
        cost = approx_token_count(text) + 20  # + the header line
        if sources and spent + cost > budget:
            continue  # a later, smaller source may still fit
        spent += cost
        sources.append(Source(marker=len(sources) + 1, hit=hit, text=text, truncated=truncated))
        if len(sources) >= settings.rag_top_k:
            break
    return sources, spent


def build_messages(question: str, sources: list[Source], version: str) -> list[Message]:
    template = load_prompt("answer", version)
    rendered = "\n\n".join(s.render() for s in sources)
    return [
        Message(role="system", content=template.format(sources=rendered)),
        Message(role="user", content=question),
    ]


def prompt_hash(messages: list[Message]) -> str:
    joined = "\n".join(f"{m.role}:{m.content}" for m in messages)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ── Citations ─────────────────────────────────────────────────────────────


def resolve_citations(answer: str, sources: list[Source]) -> tuple[str, list[Citation]]:
    """Keep the markers this request handed out; delete the rest.

    The model is shown `[S1]`…`[Sn]` and nothing else, so a marker outside that
    set is an invention. Dropping it is what keeps every citation in the API
    response resolvable to a real chunk row.
    """
    by_marker = {s.marker: s for s in sources}
    seen: dict[int, Citation] = {}

    def keep(match: re.Match[str]) -> str:
        marker = int(match.group(1))
        source = by_marker.get(marker)
        if source is None:
            return ""  # invented — strip it from the prose entirely
        seen.setdefault(marker, Citation(marker=marker, source=source))
        return match.group(0)

    cleaned = CITATION_MARKER.sub(keep, answer)
    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", cleaned)  # tidy " ." left by a removal
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, [seen[m] for m in sorted(seen)]


def _is_refusal(answer: str) -> bool:
    return answer.strip().upper().startswith(INSUFFICIENT)


def _finish(answer: str, sources: list[Source]) -> tuple[str, list[Citation], bool, bool]:
    """Normalise a raw completion into (text, citations, refused, grounded)."""
    if _is_refusal(answer):
        remainder = answer.strip()[len(INSUFFICIENT) :].lstrip(" :.-\n")
        return (
            (f"{REFUSAL_TEXT}\n\n{remainder}".strip() if remainder else REFUSAL_TEXT),
            [],
            True,
            False,
        )
    cleaned, citations = resolve_citations(answer, sources)
    return cleaned, citations, False, bool(citations)


# ── Retrieval ─────────────────────────────────────────────────────────────


@dataclass
class RetrievalOutcome:
    hits: list[SearchHit]
    above_floor: int
    sources: list[Source]
    context_tokens: int


def retrieve(
    session: Session,
    question: str,
    settings: Settings,
    *,
    embedding_provider: EmbeddingProvider | None,
    mode: str | None = None,
    top_k: int | None = None,
    filters: SearchFilters | None = None,
) -> RetrievalOutcome:
    """Phase 2 search, then the relevance floor, then context assembly."""
    result = search(
        session,
        question,
        mode=mode or settings.rag_mode,
        provider=embedding_provider,
        limit=top_k or settings.rag_top_k,
        filters=filters,
        candidates=settings.search_candidates,
        rrf_k=settings.search_rrf_k,
    )
    evidence = [h for h in result.hits if _clears_floor(h, settings.rag_min_similarity)]
    sources, tokens = ([], 0)
    if len(evidence) >= settings.rag_min_sources:
        sources, tokens = build_sources(session, evidence, settings)
    return RetrievalOutcome(
        hits=result.hits, above_floor=len(evidence), sources=sources, context_tokens=tokens
    )


def hydrate_hits(session: Session, hits: list[SearchHit]) -> None:
    """Load the documents' subjects for the response payload in one round trip."""
    ids = {h.document.id for h in hits}
    if ids:
        session.execute(
            select(Document).where(Document.id.in_(ids)).options(selectinload(Document.subjects))
        ).scalars().all()


# ── Answering ─────────────────────────────────────────────────────────────


def _config(settings: Settings, temperature: float | None, max_tokens: int | None):
    return GenerationConfig(
        temperature=settings.llm_temperature if temperature is None else temperature,
        max_output_tokens=max_tokens or settings.llm_max_output_tokens,
        context_tokens=settings.llm_context_tokens,
    )


def _refused(question: str, outcome: RetrievalOutcome, version: str) -> AnswerResult:
    """No evidence cleared the floor: refuse deterministically, with no model call."""
    return AnswerResult(
        question=question,
        answer=REFUSAL_TEXT,
        refused=True,
        grounded=False,
        retrieval_hits=len(outcome.hits),
        sources_above_floor=outcome.above_floor,
        prompt_version=version,
    )


def answer(
    session: Session,
    question: str,
    settings: Settings,
    *,
    embedding_provider: EmbeddingProvider | None,
    llm: LLMProvider,
    mode: str | None = None,
    top_k: int | None = None,
    filters: SearchFilters | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    version: str = DEFAULT_VERSION,
) -> AnswerResult:
    question = " ".join(question.split())
    if not question:
        raise ValueError("question must not be blank")

    outcome = retrieve(
        session,
        question,
        settings,
        embedding_provider=embedding_provider,
        mode=mode,
        top_k=top_k,
        filters=filters,
    )
    if not outcome.sources:
        return _refused(question, outcome, version)

    messages = build_messages(question, outcome.sources, version)
    completion = llm.complete(messages, _config(settings, temperature, max_tokens))
    text, citations, refused, grounded = _finish(completion.output, outcome.sources)
    return AnswerResult(
        question=question,
        answer=text,
        refused=refused,
        grounded=grounded,
        sources=outcome.sources,
        citations=citations,
        retrieval_hits=len(outcome.hits),
        sources_above_floor=outcome.above_floor,
        context_tokens=outcome.context_tokens,
        truncated_output=completion.truncated,
        model_id=completion.model_id,
        provider_id=getattr(llm, "provider_id", None),
        latency_ms=completion.latency_ms,
        input_tokens=completion.usage.input_tokens,
        output_tokens=completion.usage.output_tokens,
        prompt_version=version,
        prompt_hash=prompt_hash(messages),
        messages=messages,
    )


def stream_answer(
    session: Session,
    question: str,
    settings: Settings,
    *,
    embedding_provider: EmbeddingProvider | None,
    llm: LLMProvider,
    mode: str | None = None,
    top_k: int | None = None,
    filters: SearchFilters | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    version: str = DEFAULT_VERSION,
) -> Iterator[tuple[str, object]]:
    """Yield ("sources", …), then ("delta", text)…, then ("result", AnswerResult).

    Deltas are emitted as the model produces them; citations are resolved at the
    end, against the whole answer, because a marker can be split across deltas.
    """
    import time

    question = " ".join(question.split())
    if not question:
        raise ValueError("question must not be blank")

    outcome = retrieve(
        session,
        question,
        settings,
        embedding_provider=embedding_provider,
        mode=mode,
        top_k=top_k,
        filters=filters,
    )
    if not outcome.sources:
        yield "sources", []
        result = _refused(question, outcome, version)
        yield "delta", result.answer
        yield "result", result
        return

    yield "sources", outcome.sources
    messages = build_messages(question, outcome.sources, version)
    started = time.perf_counter()
    parts: list[str] = []
    for delta in llm.stream(messages, _config(settings, temperature, max_tokens)):
        parts.append(delta)
        yield "delta", delta
    raw = "".join(parts)
    text, citations, refused, grounded = _finish(raw, outcome.sources)
    yield (
        "result",
        AnswerResult(
            question=question,
            answer=text,
            refused=refused,
            grounded=grounded,
            sources=outcome.sources,
            citations=citations,
            retrieval_hits=len(outcome.hits),
            sources_above_floor=outcome.above_floor,
            context_tokens=outcome.context_tokens,
            model_id=llm.model_id,
            provider_id=getattr(llm, "provider_id", None),
            latency_ms=int((time.perf_counter() - started) * 1000),
            output_tokens=approx_token_count(raw),
            prompt_version=version,
            prompt_hash=prompt_hash(messages),
            messages=messages,
        ),
    )
