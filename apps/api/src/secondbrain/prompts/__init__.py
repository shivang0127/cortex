"""Versioned prompt files (ARCHITECTURE.md §2 — "prompts are files, not literals").

The prompts *are* the behaviour. Keeping them in files makes a change diffable,
and every `llm_calls` row records the `prompt_version` that produced it, so a
drop in answer quality is a join away from its cause rather than a guess.
"""

from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent
DEFAULT_VERSION = "v1"


@lru_cache
def load_prompt(name: str, version: str = DEFAULT_VERSION) -> str:
    path = PROMPT_DIR / version / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no prompt {name!r} at version {version!r} ({path})")
    return path.read_text(encoding="utf-8").strip()
