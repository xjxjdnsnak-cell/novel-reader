"""Shared storage helpers for Novel Reader.

This module consolidates the previously triplicated helpers
(``storage_root`` / ``book_dir`` / ``load_manifest`` / ``open_db`` /
``fetch_chunks``) that used to live in ``cli.py``, ``reading_session.py``,
and ``predictor.py`` with slightly different behavior and error messages.

Design notes:

- ``StorageError`` inherits from ``ValueError`` so that existing call sites
  that catch ``ValueError`` (in ``reading_session`` / ``predictor`` tests)
  continue to work, while ``cli.main``'s ``except (NovelReaderError, ValueError)``
  also catches it.
- ``storage_root(store=None)`` is a pure function. ``cli.storage_root(args)``
  remains a thin wrapper so that existing ``command_*`` call sites do not
  need to change.
- ``book_dir`` follows the ``cli.py`` semantics: if ``book_id`` happens to be
  an existing directory path, use it directly. This is the most permissive of
  the three previous implementations and preserves backward compatibility.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


class StorageError(ValueError):
    """Raised when a book's index files cannot be found or read.

    Inherits from ValueError so existing ``except ValueError`` blocks in
    reading_session / predictor still catch it.
    """


def storage_root(store: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the Novel Reader store directory.

    Priority: explicit ``store`` arg > ``NOVEL_READER_HOME`` env > ``./.novel-reader``.
    """
    if store:
        return Path(store).expanduser().resolve()
    env_root = os.environ.get("NOVEL_READER_HOME")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return (Path.cwd() / ".novel-reader").resolve()


def book_dir(root: Path, book_id: str) -> Path:
    """Return the directory for a given book_id.

    If ``book_id`` itself is an existing directory (e.g. a relative path was
    passed in), use it directly. Otherwise treat it as an id under ``root``.
    """
    candidate = Path(book_id)
    if candidate.exists() and candidate.is_dir():
        return candidate.resolve()
    return root / book_id


def manifest_path(root: Path, book_id: str) -> Path:
    return book_dir(root, book_id) / "manifest.json"


def load_manifest(root: Path, book_id: str) -> dict[str, Any]:
    """Load and parse ``manifest.json`` for a book.

    Raises ``StorageError`` if the manifest is missing. The message follows
    the most informative of the previously triplicated versions.
    """
    path = manifest_path(root, book_id)
    if not path.exists():
        raise StorageError(f"找不到书籍索引：{book_id}。先运行 novel-reader ingest <file>。")
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(root: Path, manifest: dict[str, Any]) -> None:
    path = manifest_path(root, manifest["book_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def db_path(root: Path, book_id: str) -> Path:
    return book_dir(root, book_id) / "index.sqlite"


def open_db(root: Path, book_id: str) -> sqlite3.Connection:
    """Open the per-book SQLite index database (read-only by default).

    The caller is responsible for closing the connection. Row factory is set
    to ``sqlite3.Row`` for dict-like access.
    """
    con = sqlite3.connect(db_path(root, book_id))
    con.row_factory = sqlite3.Row
    return con


def chapters_path(root: Path, book_id: str) -> Path:
    return book_dir(root, book_id) / "chapters.jsonl"


def fetch_chapters(root: Path, book_id: str) -> list[dict[str, Any]]:
    """Read ``chapters.jsonl`` for a book. Returns ``[]`` if the file is absent."""
    path = chapters_path(root, book_id)
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def fetch_chunks(root: Path, book_id: str) -> list[dict[str, Any]]:
    """Return all chunks for a book, ordered by chapter then chunk index."""
    con = open_db(root, book_id)
    try:
        return [
            dict(row)
            for row in con.execute(
                "SELECT * FROM chunks ORDER BY chapter_index, chunk_index"
            )
        ]
    finally:
        con.close()


# Backward-compatible alias. ``cli.py`` historically used the name
# ``fetch_all_chunks``; both names now resolve to the same implementation.
fetch_all_chunks = fetch_chunks
