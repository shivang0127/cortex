"""Managed file storage (ARCHITECTURE.md §2 — "imported files are copied into data/").

Originals live under `DATA_DIR/originals/<hash[:2]>/<hash>.<ext>`. Content-hash
naming makes the store content-addressed: importing the same bytes twice is a
no-op, two identical files from different folders share one copy, and nothing
ever depends on the user's original path again.
"""

import hashlib
from pathlib import Path

from secondbrain.config import Settings

ORIGINALS_DIR = "originals"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def url_content_hash(url: str) -> str:
    """Identity for URL sources. The content is fetched later, in the worker, so the
    URL itself is what makes a second import of the same page a no-op."""
    return hashlib.sha256(b"url:" + url.strip().encode("utf-8")).hexdigest()


def managed_relative_path(content_hash: str, extension: str) -> str:
    ext = extension.lower().lstrip(".")
    name = f"{content_hash}.{ext}" if ext else content_hash
    return f"{ORIGINALS_DIR}/{content_hash[:2]}/{name}"


def store_original(settings: Settings, data: bytes, content_hash: str, extension: str) -> str:
    """Write bytes to the managed store; returns the path relative to DATA_DIR."""
    relative = managed_relative_path(content_hash, extension)
    target = settings.data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)  # atomic on the same filesystem: no half-written originals
    return relative


def resolve_managed_path(settings: Settings, relative: str) -> Path:
    path = (settings.data_dir / relative).resolve()
    if settings.data_dir.resolve() not in path.parents:
        raise ValueError(f"storage path escapes the data directory: {relative}")
    return path


def read_original(settings: Settings, relative: str) -> bytes:
    return resolve_managed_path(settings, relative).read_bytes()


def delete_original(settings: Settings, relative: str) -> None:
    path = resolve_managed_path(settings, relative)
    if path.exists():
        path.unlink()
