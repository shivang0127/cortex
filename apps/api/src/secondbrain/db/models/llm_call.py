"""Observability for every LLM call (ARCHITECTURE.md §3, "INFRASTRUCTURE").

One row per generation: which task, which model, which prompt version, how
long, how many tokens, and whether it worked. That is what makes "answers got
worse after I changed the prompt" an answerable question instead of a feeling,
and in Phase 4 the `prompt_hash` doubles as a development cache key.

Prompts and responses are *not* stored unless `LLM_LOG_PAYLOADS=true`: the
prompt contains the user's own source text, and logging it by default would
quietly duplicate the library into a second table.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from secondbrain.db.base import Base

LLM_CALL_STATUSES = ("succeeded", "failed", "refused")


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # e.g. "rag.answer"
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(50))
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="succeeded")
    error: Mapped[str | None] = mapped_column(Text)
    # none_as_null: without it a Python None is stored as the JSONB literal `null`,
    # and `where request is null` then silently matches nothing.
    request: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )  # only when LLM_LOG_PAYLOADS
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))  # null for local models
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return f"<LlmCall {self.task} {self.model} {self.status} {self.latency_ms}ms>"
