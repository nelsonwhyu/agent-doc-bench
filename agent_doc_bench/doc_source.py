from __future__ import annotations

from pathlib import Path


def list_variants(api: str, base_dir: str | Path = "docs_library") -> list[str]:
    """Names (without .md) of doc variants that exist on disk for this api."""
    api_dir = Path(base_dir) / api
    if not api_dir.exists():
        return []
    return sorted(p.stem for p in api_dir.glob("*.md"))


def get_variant(api: str, value: str, base_dir: str | Path = "docs_library") -> str:
    """Raw content of a named doc variant, or "" if it doesn't exist — mirrors
    runner.py's previous _load_doc() behavior, which treats a missing file as
    the empty/no-doc condition rather than raising.
    """
    path = Path(base_dir) / api / f"{value}.md"
    if not path.exists():
        return ""
    return path.read_text()
