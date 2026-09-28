from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DocumentKind = Literal["pdf", "markdown", "text", "docx", "web", "youtube"]
DocumentStatus = Literal["pending", "processing", "ready", "failed"]


class SubjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    code: str | None = None
    document_count: int | None = None


class SubjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=50)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None = None
    run_after: datetime
    created_at: datetime
    finished_at: datetime | None = None


class DocumentOut(BaseModel):
    id: UUID
    kind: DocumentKind
    title: str
    origin_uri: str | None = None
    storage_path: str | None = None
    content_hash: str
    status: DocumentStatus
    error: str | None = None
    week: int | None = None
    classified_by: str
    subjects: list[SubjectOut]
    chunk_count: int
    embedded_chunk_count: int = Field(
        default=0, description="Retrieval chunks with a vector for the configured embedding model"
    )
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DocumentDetailOut(DocumentOut):
    jobs: list[JobOut]


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int


class ImportResponse(BaseModel):
    document: DocumentOut
    job: JobOut | None = None
    duplicate: bool = False


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ordinal: int
    parent_id: UUID | None = None
    heading_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    char_start: int
    char_end: int
    token_count: int
    text: str


class ChunkListOut(BaseModel):
    items: list[ChunkOut]
    total: int
