"""Characterization + regression tests for audit fixes P-5, P-6, P-7.

The goldens below (``GOLDENS`` and the explicit expected values in the
encoding test) were captured from the pre-refactor implementation and must
stay byte-identical: the perf work must never change observable output.

- P-5: ``collect_evidence`` now computes scores and reason labels in a single
  pass per chunk; evidence structure and ``reason`` strings must be unchanged.
- P-6: ingest memory reductions (raw bytes freed after decode, no
  ``splitlines`` list, progressive chapter-text release) must produce
  byte-identical manifests, chapter files, chunk rows and encodings.
- P-7: ``embed`` skips chunks that already have an embedding row for the
  current (provider, model) pair instead of re-embedding everything.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_reader import cli, predictor, search
from novel_reader.cli import read_text_file
from novel_reader.predictor import build_prediction_packet, collect_evidence
from novel_reader.storage import load_manifest, open_db

from test_reading_session import run_cli


# ---------------------------------------------------------------------------
# P-5 fixtures: synthetic chunks with known keywords (fixed inputs only).
# ---------------------------------------------------------------------------

EVIDENCE_CHUNKS = [
    {
        "chunk_id": "c0001-001",
        "chapter_index": 1,
        "chapter_title": "第一章 古钟",
        "chunk_index": 1,
        "line_start": 1,
        "line_end": 4,
        "text": "古钟低鸣，隐藏的线索指向血脉的真相。The ancient SECRET hides in the bloodline. 伏笔悄然埋下。\n下一行普通正文。",
    },
    {
        "chunk_id": "c0001-002",
        "chapter_index": 1,
        "chapter_title": "第一章 古钟",
        "chunk_index": 2,
        "line_start": 5,
        "line_end": 6,
        "text": "他立下承诺，却不知背叛已在酝酿。Betrayal and secret promises. oath oath oath.",
    },
    {
        "chunk_id": "c0002-001",
        "chapter_index": 2,
        "chapter_title": "第二章 围城",
        "chunk_index": 1,
        "line_start": 7,
        "line_end": 9,
        "text": "敌军围攻城池，冲突全面爆发，突破在即。Enemy siege, the battle breaks the gate. attack attack.",
    },
    {
        "chunk_id": "c0002-002",
        "chapter_index": 2,
        "chapter_title": "第二章 围城",
        "chunk_index": 2,
        "line_start": 10,
        "line_end": 12,
        "text": "宗门规则与境界传承决定了这场冲突的走向。Rules of the sect, ancient inheritance, bloodline map.",
    },
    {
        "chunk_id": "c0003-001",
        "chapter_index": 3,
        "chapter_title": "第三章 日常",
        "chunk_index": 1,
        "line_start": 13,
        "line_end": 14,
        "text": "集市喧闹，三餐如常，无人提及往事。Ordinary market day, cooking and travel talk.",
    },
]

# No keyword overlap with query_terms / THREAD_TERMS → exercises the
# "no chunk scored > 0" fallback branch of collect_evidence.
NEUTRAL_CHUNKS = [
    {
        "chunk_id": f"n0001-00{index}",
        "chapter_index": index,
        "chapter_title": f"第{index}章",
        "chunk_index": 1,
        "line_start": index,
        "line_end": index + 1,
        "text": f"风平浪静的一天 {index}，无人提起任何往事。plain day {index}.",
    }
    for index in range(1, 6)
]

EVIDENCE_CASES = [
    {"question": None, "scope": "general", "horizon": "next-3-chapters", "top": 3, "context_ids": set()},
    {
        "question": "血脉 secret 会不会揭露 Betrayal?",
        "scope": "foreshadowing",
        "horizon": "ending",
        "top": 2,
        "context_ids": {"c0002-002"},
    },
    {"question": None, "scope": "ending", "horizon": "ending", "top": 8, "context_ids": set()},
    {"question": "宗门规则", "scope": "character", "horizon": "next-arc", "top": 4, "context_ids": set()},
]

PACKET_ARGS = [
    SimpleNamespace(
        anchor_chapter=None,
        anchor_chunk=None,
        question="主角能否突破围攻？",
        scope="general",
        horizon="next-arc",
        context_chunks=5,
        top=8,
        semantic=False,
    ),
    SimpleNamespace(
        anchor_chapter=2,
        anchor_chunk=None,
        question=None,
        scope="foreshadowing",
        horizon="ending",
        context_chunks=3,
        top=5,
        semantic=False,
    ),
]

GOLDENS: dict[str, str] = {
    "collect_evidence_0": '[{"chapter": 1, "chapter_title": "第一章 古钟", "chunk_id": "c0001-001", "excerpt": "古钟低鸣，隐藏的线索指向血脉的真相。The ancient SECRET hides in the bloodline. 伏笔悄然埋下。 下一行普通正文。", "line_end": 4, "line_start": 1, "reason": "伏笔/线索"}, {"chapter": 2, "chapter_title": "第二章 围城", "chunk_id": "c0002-001", "excerpt": "敌军围攻城池，冲突全面爆发，突破在即。Enemy siege, the battle breaks the gate. attack attack.", "line_end": 9, "line_start": 7, "reason": "冲突升级"}, {"chapter": 2, "chapter_title": "第二章 围城", "chunk_id": "c0002-002", "excerpt": "宗门规则与境界传承决定了这场冲突的走向。Rules of the sect, ancient inheritance, bloodline map.", "line_end": 12, "line_start": 10, "reason": "设定规则"}]',
    "collect_evidence_1": '[{"chapter": 1, "chapter_title": "第一章 古钟", "chunk_id": "c0001-001", "excerpt": "古钟低鸣，隐藏的线索指向血脉的真相。The ancient SECRET hides in the bloodline. 伏笔悄然埋下。 下一行普通正文。", "line_end": 4, "line_start": 1, "reason": "伏笔/线索"}, {"chapter": 2, "chapter_title": "第二章 围城", "chunk_id": "c0002-002", "excerpt": "宗门规则与境界传承决定了这场冲突的走向。Rules of the sect, ancient inheritance, bloodline map.", "line_end": 12, "line_start": 10, "reason": "设定规则"}]',
    "collect_evidence_2": '[{"chapter": 1, "chapter_title": "第一章 古钟", "chunk_id": "c0001-001", "excerpt": "古钟低鸣，隐藏的线索指向血脉的真相。The ancient SECRET hides in the bloodline. 伏笔悄然埋下。 下一行普通正文。", "line_end": 4, "line_start": 1, "reason": "伏笔/线索"}, {"chapter": 2, "chapter_title": "第二章 围城", "chunk_id": "c0002-002", "excerpt": "宗门规则与境界传承决定了这场冲突的走向。Rules of the sect, ancient inheritance, bloodline map.", "line_end": 12, "line_start": 10, "reason": "设定规则"}, {"chapter": 1, "chapter_title": "第一章 古钟", "chunk_id": "c0001-002", "excerpt": "他立下承诺，却不知背叛已在酝酿。Betrayal and secret promises. oath oath oath.", "line_end": 6, "line_start": 5, "reason": "人物动机"}, {"chapter": 2, "chapter_title": "第二章 围城", "chunk_id": "c0002-001", "excerpt": "敌军围攻城池，冲突全面爆发，突破在即。Enemy siege, the battle breaks the gate. attack attack.", "line_end": 9, "line_start": 7, "reason": "冲突升级"}]',
    "collect_evidence_3": '[{"chapter": 1, "chapter_title": "第一章 古钟", "chunk_id": "c0001-002", "excerpt": "他立下承诺，却不知背叛已在酝酿。Betrayal and secret promises. oath oath oath.", "line_end": 6, "line_start": 5, "reason": "人物动机"}, {"chapter": 2, "chapter_title": "第二章 围城", "chunk_id": "c0002-002", "excerpt": "宗门规则与境界传承决定了这场冲突的走向。Rules of the sect, ancient inheritance, bloodline map.", "line_end": 12, "line_start": 10, "reason": "设定规则"}, {"chapter": 1, "chapter_title": "第一章 古钟", "chunk_id": "c0001-001", "excerpt": "古钟低鸣，隐藏的线索指向血脉的真相。The ancient SECRET hides in the bloodline. 伏笔悄然埋下。 下一行普通正文。", "line_end": 4, "line_start": 1, "reason": "伏笔/线索"}, {"chapter": 2, "chapter_title": "第二章 围城", "chunk_id": "c0002-001", "excerpt": "敌军围攻城池，冲突全面爆发，突破在即。Enemy siege, the battle breaks the gate. attack attack.", "line_end": 9, "line_start": 7, "reason": "冲突升级"}]',
    "collect_evidence_neutral_fallback": '[{"chapter": 3, "chapter_title": "第3章", "chunk_id": "n0001-003", "excerpt": "风平浪静的一天 3，无人提起任何往事。plain day 3.", "line_end": 4, "line_start": 3, "reason": "最近剧情状态"}, {"chapter": 5, "chapter_title": "第5章", "chunk_id": "n0001-005", "excerpt": "风平浪静的一天 5，无人提起任何往事。plain day 5.", "line_end": 6, "line_start": 5, "reason": "最近剧情状态"}, {"chapter": 4, "chapter_title": "第4章", "chunk_id": "n0001-004", "excerpt": "风平浪静的一天 4，无人提起任何往事。plain day 4.", "line_end": 5, "line_start": 4, "reason": "最近剧情状态"}]',
    "packet_0_sha256": "7944ab3ab58643b40cabccc5427466022066f89e93632feb94d7b1f67ce30998",
    "packet_1_sha256": "a228d7234c944a34912eeab183260cfba05b88d0526e6670506e6db80986cf5c",
    "packet_0_evidence": '[{"chapter": 1, "chapter_title": "Chapter 1", "chunk_id": "c0001-004", "excerpt": "ire oath betrayal clue evidence. Chapter 1 Hero enters place 1. battle conflict secret truth death breakthrough setting rule faction timeline. Alice Bob Dragon Tower Empire oath betrayal clue evidence. Chapter 1 Hero enters place 1. battle conflict secret truth death breakthrough setting rule factio", "line_end": 2, "line_start": 2, "reason": "伏笔/线索"}, {"chapter": 3, "chapter_title": "Chapter 3", "chunk_id": "c0003-004", "excerpt": "ire oath betrayal clue evidence. Chapter 3 Hero enters place 3. battle conflict secret truth death breakthrough setting rule faction timeline. Alice Bob Dragon Tower Empire oath betrayal clue evidence. Chapter 3 Hero enters place 3. battle conflict secret truth death breakthrough setting rule factio", "line_end": 8, "line_start": 8, "reason": "伏笔/线索"}, {"chapter": 4, "chapter_title": "Chapter 4", "chunk_id": "c0004-004", "excerpt": "ire oath betrayal clue evidence. Chapter 4 Hero enters place 4. battle conflict secret truth death breakthrough setting rule faction timeline. Alice Bob Dragon Tower Empire oath betrayal clue evidence. Chapter 4 Hero enters place 4. battle conflict secret truth death breakthrough setting rule factio", "line_end": 11, "line_start": 11, "reason": "伏笔/线索"}, {"chapter": 1, "chapter_title": "Chapter 1", "chunk_id": "c0001-005", "excerpt": "wer Empire oath betrayal clue evidence. Chapter 1 Hero enters place 1. battle conflict secret truth death breakthrough setting rule faction timeline. Alice Bob Dragon Tower Empire oath betrayal clue evidence. Chapter 1 Hero enters place 1. battle conflict secret truth death breakthrough setting rule", "line_end": 2, "line_start": 2, "reason": "伏笔/线索"}, {"chapter": 1, "chapter_title": "Chapter 1", "chunk_id": "c0001-006", "excerpt": "agon Tower Empire oath betrayal clue evidence. Chapter 1 Hero enters place 1. battle conflict secret truth death breakthrough setting rule faction timeline. Alice Bob Dragon Tower Empire oath betrayal clue evidence. Chapter 1 Hero enters place 1. battle conflict secret truth death breakthrough setti", "line_end": 2, "line_start": 2, "reason": "伏笔/线索"}, {"chapter": 1, "chapter_title": "Chapter 1", "chunk_id": "c0001-007", "excerpt": "Bob Dragon Tower Empire oath betrayal clue evidence. Chapter 1 Hero enters place 1. battle conflict secret truth death breakthrough setting rule faction timeline. Alice Bob Dragon Tower Empire oath betrayal clue evidence. Chapter 1 Hero enters place 1. battle conflict secret truth death breakthrough", "line_end": 2, "line_start": 2, "reason": "伏笔/线索"}, {"chapter": 1, "chapter_title": "Chapter 1", "chunk_id": "c0001-008", "excerpt": ". Alice Bob Dragon Tower Empire oath betrayal clue evidence. Chapter 1 Hero enters place 1. battle conflict secret truth death breakthrough setting rule faction timeline. Alice Bob Dragon Tower Empire oath betrayal clue evidence. Chapter 1 Hero enters place 1. battle conflict secret truth death brea", "line_end": 2, "line_start": 2, "reason": "伏笔/线索"}, {"chapter": 2, "chapter_title": "Chapter 2", "chunk_id": "c0002-004", "excerpt": "ire oath betrayal clue evidence. Chapter 2 Hero enters place 2. battle conflict secret truth death breakthrough setting rule faction timeline. Alice Bob Dragon Tower Empire oath betrayal clue evidence. Chapter 2 Hero enters place 2. battle conflict secret truth death breakthrough setting rule factio", "line_end": 5, "line_start": 5, "reason": "伏笔/线索"}]',
    "ingest_payload_sha256": "807203544cdf6fd367e5052a0d62e9fcb0dba00d90ea29d6ec3b7470bd6baf86",
    "ingest_chunks_jsonl_sha256": "7568618537c1984446b5c0a693687f6a81326c1405d22e8fa38f57b80c3b486a",
    "ingest_chapters_jsonl_sha256": "fd37c7835a9cb9c372d84f64f708265f258add85f705e1aa38818db5489ef87b",
}


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _golden(name: str) -> str:
    value = GOLDENS[name]
    assert value != "PENDING", f"golden {name} was not captured"
    return value


# ---------------------------------------------------------------------------
# P-5: predictor characterization
# ---------------------------------------------------------------------------


def test_collect_evidence_matches_captured_baseline():
    for index, case in enumerate(EVIDENCE_CASES):
        evidence = collect_evidence(
            EVIDENCE_CHUNKS,
            case["question"],
            case["scope"],
            case["horizon"],
            case["top"],
            case["context_ids"],
        )
        assert _json_dump(evidence) == _golden(f"collect_evidence_{index}")


def test_collect_evidence_zero_score_fallback_matches_captured_baseline():
    evidence = collect_evidence(NEUTRAL_CHUNKS, None, "general", "next-3-chapters", 3, set())
    assert _json_dump(evidence) == _golden("collect_evidence_neutral_fallback")
    assert [item["chunk_id"] for item in evidence] == ["n0001-003", "n0001-005", "n0001-004"]
    assert all(item["reason"] == "最近剧情状态" for item in evidence)


def test_reason_from_text_labels_match_captured_baseline():
    assert predictor.reason_from_text("宗门传承的规则从未改变") == "设定规则"
    assert predictor.reason_from_text("敌军围攻，冲突全面爆发") == "冲突升级"
    assert predictor.reason_from_text("隐藏的线索与真相") == "伏笔/线索"
    assert predictor.reason_from_text("背叛与承诺，牺牲的动机") == "人物动机"
    assert predictor.reason_from_text("完全无关的日常内容") == "最近剧情状态"
    assert predictor.reason_from_text("完全无关的日常内容", "有个问题") == "问题相关"


def test_score_terms_formula_is_preserved():
    # Pins the exact double-counting formula (lowered + case-sensitive) that
    # the single-pass implementation must reproduce.
    assert predictor.score_terms("Secret secret SECRET", ["secret"]) == 4
    assert predictor.score_terms("Dragon dragon DRAGON", ["Dragon"]) == 4
    assert predictor.score_terms("伏笔伏笔", ["伏笔"]) == 4
    assert predictor.score_terms("no match here", ["伏笔"]) == 0


def test_build_prediction_packet_matches_captured_baseline(tmp_path: Path):
    store, book = import_p5_book(tmp_path)
    for index, args in enumerate(PACKET_ARGS):
        packet = build_prediction_packet(store, book, args)
        digest = hashlib.sha256(_json_dump(packet).encode("utf-8")).hexdigest()
        assert digest == _golden(f"packet_{index}_sha256"), f"packet {index} changed"
    packet = build_prediction_packet(store, book, PACKET_ARGS[0])
    assert _json_dump(packet["evidence"]) == _golden("packet_0_evidence")


def make_p5_book(path: Path, chapters: int = 4) -> None:
    # Same shape as the shared reading-session fixture but written with
    # write_bytes so the LF endings (and therefore every char offset and
    # golden hash) are identical on every platform.
    parts = []
    for index in range(1, chapters + 1):
        body = " ".join(
            [
                f"Chapter {index}",
                f"Hero enters place {index}.",
                "battle conflict secret truth death breakthrough setting rule faction timeline.",
                "Alice Bob Dragon Tower Empire oath betrayal clue evidence.",
            ]
            * 40
        )
        parts.append(f"# Chapter {index}\n{body}\n")
    path.write_bytes("\n".join(parts).encode("utf-8"))


def import_p5_book(tmp_path: Path) -> tuple[Path, str]:
    store = tmp_path / "store"
    source = tmp_path / "p5-book.md"
    make_p5_book(source)
    run_cli(store, "ingest", str(source), "--book-id", "p5-book", "--title", "P5 Book", "--chunk-chars", "900")
    return store, "p5-book"


# ---------------------------------------------------------------------------
# P-6: ingest characterization (manifest + chunk hashes byte-identical)
# ---------------------------------------------------------------------------


def multichapter_lines() -> list[str]:
    short_sentence = "少年握紧长剑，望向远处的山门。敌军的旗帜在风中猎猎作响。"
    long_body_line = (short_sentence * 8) + "！"  # >140 chars: never a heading
    return [
        "这是正文前言，介绍这本书的来历，没有任何章节标题。",
        "",
        "# 第一章 古钟",
        long_body_line,
        "古钟低鸣，隐藏的线索指向血脉的真相。伏笔悄然埋下。",
        "",
        "# 第二章 围城",
        long_body_line,
        "敌军围攻城池，冲突全面爆发，突破在即！",
        "第2章 风云再起",
        "宗门规则与境界传承决定了这场冲突的走向。",
        "",
        "# 第三章 夜行",
        long_body_line,
        "他立下承诺，却不知背叛已在酝酿。秘密在黑暗中生长。",
        "这一行正文很长但不是标题，用来覆盖长行分支。" + short_sentence,
        "",
        "# 第四章 归途",
        long_body_line,
        "集市喧闹，三餐如常，无人提及往事。真相仍在远处等待。",
    ]


def make_multichapter_book(path: Path) -> None:
    # write_bytes (not write_text) so line endings stay LF on every platform
    # and the golden hashes below are portable.
    path.write_bytes(("\n".join(multichapter_lines()) + "\n").encode("utf-8"))


def import_p6_book(tmp_path: Path) -> tuple[Path, str, Path]:
    store = tmp_path / "store"
    source = tmp_path / "p6-book.txt"
    make_multichapter_book(source)
    run_cli(
        store,
        "ingest",
        str(source),
        "--book-id",
        "p6-book",
        "--title",
        "P6 Book",
        "--chunk-chars",
        "120",
        "--overlap-chars",
        "30",
    )
    return store, "p6-book", source


def ingest_payload(store: Path, book: str) -> dict:
    manifest = load_manifest(store, book)
    # Machine-specific fields: imported_at is a timestamp and source_path is
    # an absolute temp path; both are replaced so the golden hash only covers
    # content that must stay byte-identical.
    manifest.pop("imported_at")
    manifest["source_path"] = "<source>"
    con = open_db(store, book)
    try:
        rows = [dict(row) for row in con.execute("SELECT * FROM chunks ORDER BY chapter_index, chunk_index")]
    finally:
        con.close()
    return {"manifest": manifest, "chunks": rows}


def test_ingest_manifest_and_chunks_match_captured_baseline(tmp_path: Path):
    store, book, source = import_p6_book(tmp_path)

    payload = ingest_payload(store, book)
    # Sanity: the fixture must actually exercise many chunk boundaries.
    assert len(payload["chunks"]) >= 12
    assert payload["manifest"]["chapter_count"] == 6  # 前言 + 5 headings
    assert payload["manifest"]["total_chars"] == len("\n".join(multichapter_lines())) + 1

    digest = hashlib.sha256(_json_dump(payload).encode("utf-8")).hexdigest()
    assert digest == _golden("ingest_payload_sha256")
    assert (
        hashlib.sha256((store / book / "chunks.jsonl").read_bytes()).hexdigest()
        == _golden("ingest_chunks_jsonl_sha256")
    )
    assert (
        hashlib.sha256((store / book / "chapters.jsonl").read_bytes()).hexdigest()
        == _golden("ingest_chapters_jsonl_sha256")
    )
    assert payload["manifest"]["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_iter_lines_with_endings_matches_splitlines_exactly():
    # The old detect_chapters iterated text.splitlines(keepends=True); the
    # streaming replacement must yield the identical line strings for every
    # separator str.splitlines recognizes.
    torture_texts = [
        "",
        "\n",
        "a",
        "a\n",
        "a\r\nb\rc\vd\fe\x1cf\x1dg\x1eh\u0085i\u2028j\u2029k",
        "\r\r\n",
        "x\n\n",
        "no trailing newline",
    ]
    for text in torture_texts:
        assert list(cli.iter_lines_with_endings(text)) == text.splitlines(keepends=True), repr(text)


def test_line_starts_keeps_newline_only_semantics():
    assert cli.line_starts("") == [0]
    assert cli.line_starts("a\r\nb\r\n") == [0, 3, 6]
    # A lone \r has never produced a line-start offset (line numbers are
    # computed from \n only) — this must not change.
    assert cli.line_starts("a\rb\n") == [0, 4]


def test_detect_chapters_exotic_line_separators_match_splitlines_semantics():
    # Hand-computed from the pre-refactor splitlines-based implementation:
    # "# B\r" and "# C<U+2028>" are chapter headings because str.splitlines
    # treats lone \r and U+2028 as line breaks.
    text = "# A\r\nbody one\r\n# B\rbody two\v# C\u2028body three"
    chapters = cli.detect_chapters(text)
    assert [(c["title"], c["char_start"], c["char_end"], c["line_start"], c["line_end"]) for c in chapters] == [
        ("A", 0, 15, 1, 3),
        ("B", 15, 28, 3, 3),
        ("C", 28, 42, 5, 3),
    ]
    assert [c["text"] for c in chapters] == ["# A\r\nbody one\r\n", "# B\rbody two\v", "# C\u2028body three"]


ENCODING_CASES = [
    ("plain-utf8", "第一章 试炼\n正文内容。\n".encode("utf-8")),
    ("bom-utf8", "﻿第一章 试炼\n".encode("utf-8-sig")),
    ("gb18030", "第一章 试炼\n山河风雨，江湖夜雨十年灯。\n".encode("gb18030")),
    ("big5", "第一章 試煉\n江湖夜雨，歲月神偷。\n".encode("big5")),
    ("utf-16", "第一章 试炼\n，。！？独特字符检查。\n".encode("utf-16")),
    ("invalid-all", b"\xff\xff\xff"),
]


def test_read_text_file_encoding_detection_matches_captured_baseline(tmp_path: Path):
    expected = {
        # Captured from the pre-refactor implementation: utf-8-sig wins for any
        # valid UTF-8 (BOM or not), and gb18030 is tried before big5, so big5
        # text that is also valid gb18030 decodes as gb18030 (mojibake but
        # stable). Behavior must not change.
        "plain-utf8": ("第一章 试炼\n正文内容。\n", "utf-8-sig"),
        "bom-utf8": ("﻿第一章 试炼\n", "utf-8-sig"),
        "gb18030": ("第一章 试炼\n山河风雨，江湖夜雨十年灯。\n", "gb18030"),
        "big5": ("材\ue5e6彻 刚芬\n\ue78b打\ue7e4獴\ue4c7烦る\ue1e0敖\ue4c9\n", "gb18030"),
        "utf-16": ("第一章 试炼\n，。！？独特字符检查。\n", "utf-16"),
        "invalid-all": ("\ufffd\ufffd\ufffd", "utf-8-replace"),
    }
    for name, payload in ENCODING_CASES:
        source = tmp_path / f"{name}.txt"
        source.write_bytes(payload)
        text, encoding = read_text_file(source)
        assert (text, encoding) == expected[name], f"case {name} changed"


# ---------------------------------------------------------------------------
# P-7: embed incremental resume
# ---------------------------------------------------------------------------


@pytest.fixture
def no_local_embedding_probe(monkeypatch):
    # resolve_embedding_config probes localhost ports 8081-8085 (2s timeout
    # each) before falling back to the explicit base_url; the tests set
    # NOVEL_READER_EMBED_BASE_URL, so skipping the probe changes nothing about
    # the resolved config — it just keeps the tests fast.
    monkeypatch.setattr(search, "discover_local_qwen_embedding", lambda: None)


class CountingFakeEmbedder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, texts: list[str], provider: str, model: str) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(text)), 1.0] for text in texts]

    def seen(self) -> list[str]:
        return [text for batch in self.calls for text in batch]


def embed_args(store: Path, book: str, **overrides: object) -> SimpleNamespace:
    values = {
        "store": str(store),
        "book": book,
        "provider": "openai-compatible",
        "model": "test-model",
        "batch_size": 2,
        "max_chars": 2500,
        "limit": None,
        "quiet": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def fetch_embedding_rows(store: Path, book: str) -> list[dict]:
    con = sqlite3.connect(store / book / "index.sqlite")
    con.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in con.execute(
                "SELECT e.chunk_id, e.provider, e.model, e.vector_json, e.updated_at"
                " FROM embeddings e JOIN chunks c ON c.chunk_id = e.chunk_id"
                " ORDER BY c.chapter_index, c.chunk_index"
            )
        ]
    finally:
        con.close()
    return rows


def count_chunks(store: Path, book: str) -> int:
    con = sqlite3.connect(store / book / "index.sqlite")
    try:
        return con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        con.close()


def test_embed_resume_skips_already_embedded_chunks(tmp_path: Path, monkeypatch, no_local_embedding_probe):
    monkeypatch.setenv("NOVEL_READER_EMBED_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("NOVEL_READER_EMBED_API_KEY", "test-key")
    monkeypatch.setenv("NOVEL_READER_EMBED_MODEL", "test-model")

    store, book, _source = import_p6_book(tmp_path)
    total = count_chunks(store, book)
    con = sqlite3.connect(store / book / "index.sqlite")
    try:
        first_chunk_id, first_chunk_text = con.execute(
            "SELECT chunk_id, text FROM chunks ORDER BY chapter_index, chunk_index LIMIT 1"
        ).fetchone()
        con.execute(
            "INSERT INTO embeddings (chunk_id, provider, model, vector_json, updated_at)"
            " VALUES (?, 'openai-compatible', 'test-model', '[3.0, 4.0]', '2020-01-01T00:00:00+00:00')",
            (first_chunk_id,),
        )
        con.commit()
    finally:
        con.close()

    fake = CountingFakeEmbedder()
    monkeypatch.setattr(cli, "embed_texts", fake)

    result = cli.command_embed(embed_args(store, book))

    assert result == {
        "ok": True,
        "provider": "openai-compatible",
        "model": "test-model",
        "chunks": total - 1,
    }
    # The pre-existing row's chunk text was never sent to the embedder.
    assert first_chunk_text not in fake.seen()
    assert len(fake.seen()) == total - 1
    # Batches keep the chapter/chunk ordering and the batch size.
    for batch in fake.calls[:-1]:
        assert len(batch) == 2
    rows = {row["chunk_id"]: row for row in fetch_embedding_rows(store, book)}
    assert len(rows) == total
    assert rows[first_chunk_id]["vector_json"] == "[3.0, 4.0]"
    assert rows[first_chunk_id]["updated_at"] == "2020-01-01T00:00:00+00:00"
    other = next(row for chunk_id, row in rows.items() if chunk_id != first_chunk_id)
    assert other["provider"] == "openai-compatible"
    assert other["model"] == "test-model"

    manifest = load_manifest(store, book)
    assert manifest["embedding"]["chunk_count"] == total
    assert manifest["embedding"]["provider"] == "openai-compatible"
    assert manifest["embedding"]["model"] == "test-model"


def test_embed_fully_embedded_book_is_a_no_op(tmp_path: Path, monkeypatch, no_local_embedding_probe):
    monkeypatch.setenv("NOVEL_READER_EMBED_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("NOVEL_READER_EMBED_API_KEY", "test-key")
    monkeypatch.setenv("NOVEL_READER_EMBED_MODEL", "test-model")

    store, book, _source = import_p6_book(tmp_path)
    # Populate embeddings with a fake embedder (the real one would need an
    # HTTP endpoint); a fresh fake then measures the resume run.
    monkeypatch.setattr(cli, "embed_texts", CountingFakeEmbedder())
    cli.command_embed(embed_args(store, book, batch_size=3))
    rows_before = fetch_embedding_rows(store, book)
    assert len(rows_before) == count_chunks(store, book)

    # Second run with every chunk already embedded: the embedder must not be
    # called at all.
    fake = CountingFakeEmbedder()
    monkeypatch.setattr(cli, "embed_texts", fake)
    result = cli.command_embed(embed_args(store, book, batch_size=3))

    assert fake.calls == []
    assert result["chunks"] == 0
    assert fetch_embedding_rows(store, book) == rows_before
    manifest = load_manifest(store, book)
    assert manifest["embedding"]["chunk_count"] == len(rows_before)


def test_embed_different_model_reembeds_everything(tmp_path: Path, monkeypatch, no_local_embedding_probe):
    monkeypatch.setenv("NOVEL_READER_EMBED_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("NOVEL_READER_EMBED_API_KEY", "test-key")
    monkeypatch.setenv("NOVEL_READER_EMBED_MODEL", "test-model")

    store, book, _source = import_p6_book(tmp_path)
    monkeypatch.setattr(cli, "embed_texts", CountingFakeEmbedder())
    cli.command_embed(embed_args(store, book))
    total = count_chunks(store, book)

    fake = CountingFakeEmbedder()
    monkeypatch.setattr(cli, "embed_texts", fake)
    result = cli.command_embed(embed_args(store, book, model="other-model"))

    assert result["chunks"] == total
    assert len(fake.seen()) == total
    # embeddings PK is chunk_id, so the new model's rows replace the old ones.
    rows = fetch_embedding_rows(store, book)
    assert len(rows) == total
    assert all(row["model"] == "other-model" for row in rows)
