from typing import Annotated

from fastapi import APIRouter, Depends

from secondbrain.config import Settings, get_settings
from secondbrain.providers.llm import get_llm_provider, llm_enabled
from secondbrain.schemas.rag import LLMStatusOut

router = APIRouter(prefix="/llm", tags=["llm"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/status", response_model=LLMStatusOut, summary="Is the language model ready?")
def llm_status(settings: SettingsDep) -> LLMStatusOut:
    """Always 200 — it describes an unavailable runtime rather than failing like one."""
    if not llm_enabled(settings):
        return LLMStatusOut(enabled=False, detail="LLM_PROVIDER=none")
    health = get_llm_provider().health()
    return LLMStatusOut(
        enabled=True,
        provider=health.provider_id,
        model=health.model_id,
        reachable=health.reachable,
        model_available=health.model_available,
        context_window=health.context_window,
        max_output_tokens=settings.llm_max_output_tokens,
        detail=health.detail,
    )
