from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

AskMode = Literal["hybrid", "semantic", "keyword"]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: AskMode | None = Field(default=None, description="Retrieval mode; defaults to RAG_MODE")
    top_k: int | None = Field(default=None, ge=1, le=20)
    subject_id: UUID | None = None
    week: int | None = Field(default=None, ge=0)
    kind: Literal["pdf", "markdown", "text", "docx", "web", "youtube"] | None = None
    document_id: UUID | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=32, le=4096)


class SourceOut(BaseModel):
    """A numbered block of context, and where it came from."""

    marker: int = Field(description="The [S<marker>] the answer may cite")
    chunk_id: UUID
    document_id: UUID
    document_title: str
    document_kind: str
    origin_uri: str | None = None
    subjects: list[str]
    week: int | None = None
    heading_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    char_start: int
    char_end: int
    ordinal: int
    text: str = Field(description="Exactly the text the model was shown for this source")
    truncated: bool = False
    score: float
    similarity: float | None = None
    semantic_rank: int | None = None
    keyword_rank: int | None = None


class CitationOut(BaseModel):
    """A marker the model used that resolved to a real retrieved chunk."""

    marker: int
    chunk_id: UUID
    document_id: UUID
    document_title: str
    heading_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    char_start: int
    char_end: int
    text: str


class RetrievalInfo(BaseModel):
    mode: AskMode
    embedding_model: str | None = None
    hits: int = Field(description="Chunks returned by Phase 2 retrieval")
    above_floor: int = Field(description="…of those, the ones that cleared the relevance floor")
    min_similarity: float
    top_k: int
    context_tokens: int


class GenerationInfo(BaseModel):
    provider: str | None = None
    model: str | None = None
    prompt_version: str
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    truncated: bool = False


class AskResponse(BaseModel):
    question: str
    answer: str
    refused: bool = Field(
        description="The library had too little evidence; no answer was generated"
    )
    grounded: bool = Field(description="The answer carries at least one valid citation")
    citations: list[CitationOut]
    sources: list[SourceOut]
    retrieval: RetrievalInfo
    generation: GenerationInfo


class LLMStatusOut(BaseModel):
    enabled: bool
    provider: str | None = None
    model: str | None = None
    reachable: bool = False
    model_available: bool = False
    context_window: int = 0
    max_output_tokens: int = 0
    detail: str | None = None
