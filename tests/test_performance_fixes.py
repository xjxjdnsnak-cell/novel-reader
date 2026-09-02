"""Regression tests for the audit performance fixes (P-1, P-2, P-3)."""

from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path

import pytest

from novel_reader import reading_session, search
from novel_reader.cli import (
    STYLE_NGRAM_TOP_K,
    StyleStatsAccumulator,
    text_stats,
)
from novel_reader.storage import fetch_chunks, load_manifest

from test_reading_session import import_book, load_json, run_cli

CHUNK_PACKET_KEYS = (
    "chunk_id",
    "chapter_index",
    "chapter_title",
    "chunk_index",
    "line_start",
    "line_end",
    "text",
)


def projected(chunks: list[dict]) -> list[dict]:
    return [{key: chunk[key] for key in CHUNK_PACKET_KEYS} for chunk in chunks]


# ---------------------------------------------------------------------------
# P-1: targeted chapter chunk fetching
# ---------------------------------------------------------------------------


def test_fetch_chunks_chapter_filter_returns_only_that_chapter(tmp_path: Path):
    store, book = import_book(tmp_path)

    all_chunks = fetch_chunks(store, book)
    chapter_chunks = fetch_chunks(store, book, chapter=2)

    assert chapter_chunks, "fixture should have chunks in chapter 2"
    assert all(chunk["chapter_index"] == 2 for chunk in chapter_chunks)
    assert chapter_chunks == [chunk for chunk in all_chunks if chunk["chapter_index"] == 2]
    assert len(all_chunks) > len(chapter_chunks)
    # No filter still loads the whole book.
    assert fetch_chunks(store, book) == all_chunks


def test_build_read_next_queries_only_target_chapters(tmp_path: Path, monkeypatch):
    store, book = import_book(tmp_path)
    session = load_json(run_cli(store, "read-session", book, "--mode", "survey", "--json"))
    session_id = session["session_id"]

    real_fetch = reading_session.fetch_chunks
    calls: list[int | None] = []

    def spy(root: Path, book_id: str, chapter: int | None = None):
        calls.append(chapter)
        return real_fetch(root, book_id, chapter)

    monkeypatch.setattr(reading_session, "fetch_chunks", spy)

    packet = reading_session.build_read_next(store, session_id)
    assert calls == [1], "read-next must fetch only the target chapter, not the whole book"
    chapter_one = packet["chapters"][0]
    full_chunks = real_fetch(store, book)
    by_chapter = reading_session.group_chunks_by_chapter(full_chunks)
    assert chapter_one["chunks"] == projected(by_chapter[chapter_one["chapter_index"]])

    manual = reading_session.build_read_next(store, session_id, chapter=3)
    assert calls == [1, 3]
    assert manual["chapters"][0]["chunks"] == projected(by_chapter[3])

    batch = reading_session.build_read_next(store, session_id, batch_chapters=2)
    assert [item["chapter_index"] for item in batch["chapters"]] == [1, 2]
    for item in batch["chapters"]:
        assert item["chunks"] == projected(by_chapter[item["chapter_index"]])


# ---------------------------------------------------------------------------
# P-2: skip like_search when FTS is saturated
# ---------------------------------------------------------------------------


def make_fts_book(path: Path) -> None:
    dragon_body = " ".join(
        ["the dragon circled the tower and guards fled.", "dragon fire lit the night sky."] * 4
    )
    parts = [
        f"# Chapter 1\n{dragon_body}\n",
        f"# Chapter 2\n{dragon_body}\n",
        "# Chapter 3\nthe dragonborn warrior forged his own legend without any winged beast.\n",
    ]
    path.write_text("\n".join(parts), encoding="utf-8")


def import_fts_book(tmp_path: Path) -> tuple[Path, str]:
    store = tmp_path / "store"
    source = tmp_path / "book.md"
    make_fts_book(source)
    run_cli(store, "ingest", str(source), "--book-id", "fts-book", "--title", "FTS Book")
    return store, "fts-book"


def test_search_book_skips_like_when_fts_saturated(tmp_path: Path, monkeypatch):
    store, book = import_fts_book(tmp_path)
    assert load_manifest(store, book).get("fts_enabled") is True

    real_like = search.like_search
    calls: list[int] = []

    def spy(con, query, top, context_chars):
        calls.append(1)
        return real_like(con, query, top, context_chars)

    monkeypatch.setattr(search, "like_search", spy)

    # FTS finds both dragon chunks; the LIKE pass must be skipped entirely.
    saturated = search.search_book(store, book, "dragon", top=2, context_chars=80)
    assert calls == []
    assert len(saturated) == 2
    assert all(item["source"] == "fts" for item in saturated)
    assert {item["chapter_index"] for item in saturated} == {1, 2}
    # The dragonborn chunk would only be found by LIKE substring matching;
    # it must be absent while LIKE is skipped.
    assert all("dragonborn" not in item["snippet"] for item in saturated)

    # With a larger top the FTS result set no longer saturates, so LIKE runs
    # and now surfaces the dragonborn chunk.
    widened = search.search_book(store, book, "dragon", top=10, context_chars=80)
    assert calls, "LIKE must still run when FTS does not return top results"
    dragonborn = [item for item in widened if item["chapter_index"] == 3]
    assert dragonborn and all(item["source"] == "like" for item in dragonborn)


# ---------------------------------------------------------------------------
# P-2: numpy-batched semantic search with graceful fallback
# ---------------------------------------------------------------------------

QUERY_VECTOR = [1.0, 0.5, -0.25, 2.0]
BOOK_VECTORS = [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
    [1.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, -1.0, 0.0],
    [0.5, 0.5, 0.5, 0.5],
]


def test_batched_cosine_scores_matches_pure_python_cosine():
    pytest.importorskip("numpy")
    rows = ['[1,0,2,3]', '[0,1,1,0]', '[2,0,4,6]', '[0,0,0,0]']
    query = [1.0, 0.0, 2.0, 3.0]
    batched = search.batched_cosine_scores(query, rows)
    assert batched is not None
    assert batched == [round(search.cosine(query, json.loads(row)), 6) for row in rows]
    # Degenerate inputs fall back to the pure-Python path.
    assert search.batched_cosine_scores(query, []) == []
    assert search.batched_cosine_scores(query, ['[1,2]']) is None


def test_semantic_search_numpy_and_pure_python_paths_agree(tmp_path: Path, monkeypatch):
    pytest.importorskip("numpy")
    store, book = import_book(tmp_path)
    chunks = fetch_chunks(store, book)
    assert len(chunks) >= len(BOOK_VECTORS)

    con = sqlite3.connect(store / book / "index.sqlite")
    try:
        for index, chunk in enumerate(chunks):
            vector = BOOK_VECTORS[index % len(BOOK_VECTORS)]
            con.execute(
                "INSERT OR REPLACE INTO embeddings (chunk_id, provider, model, vector_json, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    chunk["chunk_id"],
                    "test-provider",
                    "test-model",
                    json.dumps(vector),
                    "2026-01-01T00:00:00+00:00",
                ),
            )
        con.commit()
    finally:
        con.close()

    monkeypatch.setattr(
        search, "embed_texts", lambda texts, provider, model: [list(QUERY_VECTOR)]
    )
    # Sanity: the numpy path is really available in this environment.
    assert search.batched_cosine_scores(list(QUERY_VECTOR), ["[1,0,0,0]"]) is not None

    numpy_results = search.semantic_search(
        store, book, "hero battle", "test-provider", "test-model", top=5, context_chars=80
    )
    assert numpy_results

    monkeypatch.setattr(search, "batched_cosine_scores", lambda *args, **kwargs: None)
    pure_results = search.semantic_search(
        store, book, "hero battle", "test-provider", "test-model", top=5, context_chars=80
    )

    assert [item["chunk_id"] for item in numpy_results] == [item["chunk_id"] for item in pure_results]
    for numpy_item, pure_item in zip(numpy_results, pure_results):
        assert abs(numpy_item["score"] - pure_item["score"]) <= 1e-6
        assert numpy_item["source"] == pure_item["source"] == "embedding"


# ---------------------------------------------------------------------------
# P-3: streaming style stats
# ---------------------------------------------------------------------------


def test_style_stats_accumulator_matches_joined_text_stats():
    rng = random.Random(7)
    words = ["风云", "宗门", "剑光", "少年", "暗影", "破军", "灵力", "石塔", "longword", "data123"]
    terminals = ["。", "！", "？", "…", "；", "\n", " ", ""]
    quotes = ["“对白内容”", "他说：好。", "「引语」"]

    for _ in range(120):
        chunks = []
        for _ in range(rng.randint(0, 6)):
            parts = []
            for _ in range(rng.randint(0, 8)):
                sentence = "".join(rng.choice(words) for _ in range(rng.randint(1, 6)))
                if rng.random() < 0.3:
                    sentence += rng.choice(quotes)
                parts.append(sentence)
            if parts and rng.random() < 0.7:
                ending = rng.choice(terminals)
                if ending:
                    parts[-1] += ending
            chunks.append("".join(parts))
        joined = "\n\n".join(chunks)

        accumulator = StyleStatsAccumulator()
        for chunk in chunks:
            accumulator.add(chunk)

        assert accumulator.stats() == text_stats(joined)


def test_style_stats_accumulator_prunes_ngrams_to_top_k():
    accumulator = StyleStatsAccumulator()
    for index in range(1800):
        base = 0x4E00 + index * 4
        accumulator.add("".join(chr(base + offset) for offset in range(4)) + "。")

    assert len(accumulator._ngrams) <= STYLE_NGRAM_TOP_K
    stats = accumulator.stats()
    assert len(stats["top_terms"]) == 20
    assert stats["chars"] == 1800 * 5 + 2 * 1799
