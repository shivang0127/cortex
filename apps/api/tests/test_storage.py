from pathlib import Path

import pytest

from secondbrain.config import Settings
from secondbrain.services import storage


def test_sha256_and_url_hash_are_stable() -> None:
    assert storage.sha256_hex(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert storage.url_content_hash("https://x.test/a ") == storage.url_content_hash(
        "https://x.test/a"
    )
    assert storage.url_content_hash("https://x.test/a") != storage.sha256_hex(b"https://x.test/a")


def test_store_original_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    data = b"%PDF-1.4 pretend"
    digest = storage.sha256_hex(data)
    relative = storage.store_original(settings, data, digest, ".PDF")
    assert relative == f"originals/{digest[:2]}/{digest}.pdf"
    path = tmp_path / relative
    assert path.read_bytes() == data
    mtime = path.stat().st_mtime_ns
    assert storage.store_original(settings, data, digest, ".pdf") == relative
    assert path.stat().st_mtime_ns == mtime, "an existing copy is never rewritten"
    assert not list(tmp_path.rglob("*.part")), "no temp files left behind"
    assert storage.read_original(settings, relative) == data
    storage.delete_original(settings, relative)
    assert not path.exists()


def test_paths_cannot_escape_the_data_directory(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    with pytest.raises(ValueError):
        storage.resolve_managed_path(settings, "../../etc/passwd")


def test_like_pattern_escapes_wildcards() -> None:
    from secondbrain.services.documents import _like_pattern

    assert _like_pattern("Automata") == "%Automata%"
    assert _like_pattern("100%") == "%100" + chr(92) + "%%"
    assert _like_pattern("a_b") == "%a" + chr(92) + "_b%"
    assert _like_pattern("back" + chr(92) + "slash") == "%back" + chr(92) * 2 + "slash%"
