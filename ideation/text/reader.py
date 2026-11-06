"""Read-only access to the Scraper's extracted document text (05 O6).

``<DOCUMENTS_ROOT>/data/documents/<TICKER>/<doc_type>/<name>.jsonl.gz`` holds one JSON line per page
(``{"page": n, "text": ...}``). This is the only module that touches those files, always via ``gzip.open(..., 'rt')``.
A catalog ``text_path`` that is absolute, contains ``..`` or does not sit under ``data/documents`` is refused.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

DOCS_SUBDIR = ("data", "documents")


class UnsafeDocumentPathError(ValueError):
    """A catalog path tried to leave ``<DOCUMENTS_ROOT>/data/documents``."""


def documents_dir(root: Path) -> Path:
    return Path(root).expanduser().joinpath(*DOCS_SUBDIR)


def resolve_text_path(root: Path, text_path: str) -> Path:
    """Absolute path of ``text_path`` under ``<root>/data/documents``; raises if it would escape that directory."""
    rel = Path(text_path)
    base = documents_dir(root)
    if rel.is_absolute() or ".." in rel.parts or tuple(rel.parts[:2]) != DOCS_SUBDIR or len(rel.parts) < 3:
        raise UnsafeDocumentPathError(text_path)
    full = (Path(root).expanduser() / rel).resolve()
    if not full.is_relative_to(base.resolve()):  # symlink escape
        raise UnsafeDocumentPathError(text_path)
    return full


def read_pages(root: Path, text_path: str) -> list[str]:
    """Page texts in page order. Raises ``UnsafeDocumentPathError`` or ``OSError`` (missing or corrupt file)."""
    pages: list[tuple[int, str]] = []
    with gzip.open(resolve_text_path(root, text_path), "rt", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            pages.append((int(rec.get("page", i + 1)), str(rec.get("text") or "")))
    pages.sort(key=lambda p: p[0])
    return [t for _, t in pages]
