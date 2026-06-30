from __future__ import annotations

import datetime as dt
import json
import math
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .storage import (
    StorageError,
    book_dir,
    fetch_chapters,
    fetch_chunks,
    load_manifest,
)


LEVEL_NONE = "NONE"
LEVEL_L1 = "L1_SKIMMED"
LEVEL_L2 = "L2_READ"
LEVEL_L3 = "L3_DEEP_READ"
LEVEL_ORDER = {
    LEVEL_NONE: 0,
    LEVEL_L1: 1,
    LEVEL_L2: 2,
    LEVEL_L3: 3,
}

MODES = {"survey", "balanced", "deep"}
GOALS = {"full"}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def reading_db_path(root: Path) -> Path:
    return root / "reading.sqlite"


def open_reading_db(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(reading_db_path(root))
    con.row_factory = sqlite3.Row
    init_reading_db(con)
    return con


def init_reading_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS reading_sessions (
          session_id TEXT PRIMARY KEY,
          book_id TEXT NOT NULL,
          goal TEXT NOT NULL,
          mode TEXT NOT NULL,
          target_chapters TEXT NOT NULL,
          key_chapters TEXT NOT NULL,
          l2_required TEXT NOT NULL,
          l3_required TEXT NOT NULL,
          current_chapter INTEGER NOT NULL,
          status TEXT NOT NULL,
          deep_ratio REAL NOT NULL,
          query TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          finalized_at TEXT
        );

        CREATE TABLE IF NOT EXISTS chapter_read_logs (
          session_id TEXT NOT NULL,
          book_id TEXT NOT NULL,
          chapter_index INTEGER NOT NULL,
          required_level TEXT NOT NULL,
          coverage_level TEXT NOT NULL,
          status TEXT NOT NULL,
          chunk_ids TEXT NOT NULL,
          summary_path TEXT,
          quality_json TEXT,
          manual_override INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (session_id, chapter_index)
        );
        """
    )
    con.commit()


def group_chunks_by_chapter(chunks: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for chunk in chunks:
        grouped.setdefault(int(chunk["chapter_index"]), []).append(chunk)
    return grouped


def score_chapter_importance(chapter_text: str, chapter_meta: dict[str, Any], query: str | None = None) -> dict[str, Any]:
    text = chapter_text.lower()
    proper_names = len(re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", chapter_text))
    conflict_terms = ("battle", "fight", "conflict", "kill", "death", "betrayal", "war", "attack")
    setting_terms = ("rule", "setting", "system", "faction", "empire", "tower", "power", "level")
    secret_terms = ("secret", "truth", "clue", "mystery", "breakthrough", "oath", "hidden")
    chinese_combat = ("杀", "血", "剑", "刀", "枪", "拳", "伤", "死", "敌", "战", "冲", "退", "爆", "轰", "斩", "逃", "追", "围攻")
    chinese_reversal = ("真相", "秘密", "线索", "发现", "原来", "竟然", "背叛", "隐藏", "暴露", "谜", "疑点", "异常", "预感")
    chinese_setting = ("规则", "境界", "功法", "宗门", "势力", "组织", "等级", "系统", "能力", "法术", "灵力", "血脉", "传承")
    chinese_relation = ("沉默", "眼泪", "喜欢", "恨", "告别", "牺牲", "承诺", "误会", "心中", "愧疚", "愤怒", "恐惧")
    chinese_progress = ("离开", "进入", "突破", "失败", "选择", "代价", "决定", "出发", "抵达", "失踪", "死亡", "重逢")
    reasons: list[str] = []
    score = len(chapter_text) / 1000.0
    if chapter_text:
        reasons.append("chapter_length")
    if proper_names:
        score += min(proper_names, 40) * 0.2
        reasons.append("proper_name_density")
    buckets = (
        ("combat_keywords", conflict_terms, 1.5, text),
        ("setting_keywords", setting_terms, 1.0, text),
        ("reversal_keywords", secret_terms, 1.2, text),
        ("combat_keywords", chinese_combat, 0.9, chapter_text),
        ("reversal_keywords", chinese_reversal, 1.4, chapter_text),
        ("setting_keywords", chinese_setting, 1.1, chapter_text),
        ("relationship_keywords", chinese_relation, 0.8, chapter_text),
        ("plot_progress_keywords", chinese_progress, 1.0, chapter_text),
    )
    for reason, terms, weight, haystack in buckets:
        hits = sum(haystack.count(term) for term in terms)
        if hits:
            score += hits * weight
            if reason not in reasons:
                reasons.append(reason)
    if query:
        for term in re.findall(r"\w+", query.lower()):
            if len(term) >= 2:
                hits = text.count(term)
                if hits:
                    score += hits * 2.0
                    if "query_matches" not in reasons:
                        reasons.append("query_matches")
    score += int(chapter_meta.get("chapter_index", 0)) * 0.001
    return {"score": round(score, 4), "reasons": reasons}


def choose_key_chapters(
    chapters: list[dict[str, Any]],
    chunks_by_chapter: dict[int, list[dict[str, Any]]],
    ratio: float,
    query: str | None,
) -> list[int]:
    if not chapters:
        return []
    count = max(1, math.ceil(len(chapters) * max(0.0, min(ratio, 1.0))))
    scored = []
    for chapter in chapters:
        index = int(chapter["chapter_index"])
        text = "\n".join(chunk["text"] for chunk in chunks_by_chapter.get(index, []))
        scored.append((score_chapter_importance(text, chapter, query)["score"], index))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return sorted(index for _, index in scored[:count])


def required_level_for(chapter: int, l2_required: set[int], l3_required: set[int]) -> str:
    if chapter in l3_required:
        return LEVEL_L3
    if chapter in l2_required:
        return LEVEL_L2
    return LEVEL_L1


def create_session(
    root: Path,
    book_id: str,
    goal: str,
    mode: str,
    deep_ratio: float,
    query: str | None = None,
    focus_chapter: int | None = None,
    after_chapter: int | None = None,
) -> dict[str, Any]:
    if goal not in GOALS:
        raise ValueError("--goal currently supports only full")
    if mode not in MODES:
        raise ValueError("--mode must be survey, balanced, or deep")
    manifest = load_manifest(root, book_id)
    chapters = fetch_chapters(root, book_id)
    chunks = fetch_chunks(root, book_id)
    chunks_by_chapter = group_chunks_by_chapter(chunks)
    target_chapters = [int(chapter["chapter_index"]) for chapter in chapters]

    key_chapters: list[int] = []
    l2_required: set[int] = set()
    l3_required: set[int] = set()
    if mode in {"balanced", "deep"}:
        key_chapters = choose_key_chapters(chapters, chunks_by_chapter, deep_ratio, query)
        l2_required.update(key_chapters)
    if mode == "deep":
        anchor = focus_chapter or after_chapter
        if anchor is None and key_chapters:
            anchor = key_chapters[0]
        if anchor is not None:
            for chapter in (anchor - 1, anchor, anchor + 1):
                if chapter in target_chapters:
                    l3_required.add(chapter)
                    l2_required.add(chapter)

    session_id = f"rs-{uuid.uuid4().hex[:12]}"
    stamp = now_iso()
    con = open_reading_db(root)
    try:
        con.execute(
            """
            INSERT INTO reading_sessions (
              session_id, book_id, goal, mode, target_chapters, key_chapters,
              l2_required, l3_required, current_chapter, status, deep_ratio,
              query, created_at, updated_at, finalized_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                session_id,
                book_id,
                goal,
                mode,
                json.dumps(target_chapters),
                json.dumps(key_chapters),
                json.dumps(sorted(l2_required)),
                json.dumps(sorted(l3_required)),
                target_chapters[0] if target_chapters else 0,
                "active",
                deep_ratio,
                query,
                stamp,
                stamp,
            ),
        )
        for chapter in target_chapters:
            chunk_ids = [chunk["chunk_id"] for chunk in chunks_by_chapter.get(chapter, [])]
            con.execute(
                """
                INSERT INTO chapter_read_logs (
                  session_id, book_id, chapter_index, required_level, coverage_level,
                  status, chunk_ids, summary_path, quality_json, manual_override, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?)
                """,
                (
                    session_id,
                    book_id,
                    chapter,
                    required_level_for(chapter, l2_required, l3_required),
                    LEVEL_NONE,
                    "pending",
                    json.dumps(chunk_ids),
                    stamp,
                ),
            )
        con.commit()
    finally:
        con.close()

    return {
        "ok": True,
        "session_id": session_id,
        "book_id": book_id,
        "goal": goal,
        "mode": mode,
        "chapter_count": int(manifest["chapter_count"]),
        "target_chapters": target_chapters,
        "key_chapters": key_chapters,
        "l2_required": sorted(l2_required),
        "l3_required": sorted(l3_required),
        "next_chapter": target_chapters[0] if target_chapters else None,
    }


def get_session(root: Path, session_id: str) -> sqlite3.Row:
    con = open_reading_db(root)
    try:
        row = con.execute("SELECT * FROM reading_sessions WHERE session_id = ?", (session_id,)).fetchone()
        if not row:
            raise ValueError(f"Reading session not found: {session_id}")
        return row
    finally:
        con.close()


def session_logs(root: Path, session_id: str) -> list[sqlite3.Row]:
    con = open_reading_db(root)
    try:
        return list(
            con.execute(
                "SELECT * FROM chapter_read_logs WHERE session_id = ? ORDER BY chapter_index",
                (session_id,),
            )
        )
    finally:
        con.close()


def find_latest_session(root: Path, book_id: str) -> sqlite3.Row | None:
    con = open_reading_db(root)
    try:
        return con.execute(
            "SELECT * FROM reading_sessions WHERE book_id = ? ORDER BY created_at DESC LIMIT 1",
            (book_id,),
        ).fetchone()
    finally:
        con.close()


def note_schema(level: str) -> dict[str, Any]:
    if level == LEVEL_L1:
        return {
            "level": level,
            "fields": ["one_sentence", "events", "characters", "evidence_chunks"],
            "min_chars": 100,
        }
    if level == LEVEL_L2:
        return {
            "level": level,
            "fields": ["事件", "人物与动机", "冲突", "情节因果", "伏笔/回收", "设定/地点/势力", "时间线", "写作观察", "证据块"],
            "min_chars": 300,
        }
    return {
        "level": level,
        "fields": [
            "事件",
            "人物与动机",
            "冲突",
            "情节因果",
            "伏笔/回收",
            "设定/地点/势力",
            "时间线",
            "写作观察",
            "证据块",
            "scene_breakdown",
            "style_observation",
            "character_state",
            "continuity_constraints",
        ],
        "min_chars": 600,
    }


def build_read_next(root: Path, session_id: str, batch_chapters: int = 1, chapter: int | None = None) -> dict[str, Any]:
    session = get_session(root, session_id)
    logs = session_logs(root, session_id)
    target = None
    if chapter is not None:
        target = [row for row in logs if int(row["chapter_index"]) == chapter]
        if not target:
            raise ValueError(f"Chapter {chapter} is not in this reading session.")
    else:
        target = [
            row for row in logs
            if LEVEL_ORDER[row["coverage_level"]] < LEVEL_ORDER[row["required_level"]]
        ][: max(1, batch_chapters)]
    if not target:
        return {
            "ok": True,
            "session_id": session_id,
            "book_id": session["book_id"],
            "chapters": [],
            "note_schema": None,
            "next_allowed_action": "finalize-reading",
        }

    chunks_by_chapter = group_chunks_by_chapter(fetch_chunks(root, session["book_id"]))
    con = open_reading_db(root)
    try:
        for row in target:
            con.execute(
                """
                UPDATE chapter_read_logs
                SET status = 'issued', manual_override = ?, updated_at = ?
                WHERE session_id = ? AND chapter_index = ?
                """,
                (1 if chapter is not None else int(row["manual_override"]), now_iso(), session_id, int(row["chapter_index"])),
            )
        con.execute(
            "UPDATE reading_sessions SET current_chapter = ?, updated_at = ? WHERE session_id = ?",
            (int(target[0]["chapter_index"]), now_iso(), session_id),
        )
        con.commit()
    finally:
        con.close()

    chapters = []
    for row in target:
        chapter_index = int(row["chapter_index"])
        chunks = chunks_by_chapter.get(chapter_index, [])
        chapters.append(
            {
                "chapter_index": chapter_index,
                "required_level": row["required_level"],
                "coverage_level": row["coverage_level"],
                "manual_override": bool(chapter is not None),
                "chunks": [
                    {
                        "chunk_id": chunk["chunk_id"],
                        "chapter_index": chunk["chapter_index"],
                        "chapter_title": chunk["chapter_title"],
                        "chunk_index": chunk["chunk_index"],
                        "line_start": chunk["line_start"],
                        "line_end": chunk["line_end"],
                        "text": chunk["text"],
                    }
                    for chunk in chunks
                ],
            }
        )

    return {
        "ok": True,
        "session_id": session_id,
        "book_id": session["book_id"],
        "chapters": chapters,
        "note_schema": note_schema(target[0]["required_level"]),
        "next_allowed_action": "submit-note",
    }


def compact_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def contains_any(text: str, names: list[str]) -> bool:
    lowered = text.lower()
    return any(name.lower() in lowered for name in names)


def field_aliases(level: str) -> list[tuple[str, list[str]]]:
    l1 = [
        ("one_sentence", ["one_sentence", "一句话", "一句话总结"]),
        ("events", ["events", "事件"]),
        ("characters", ["characters", "人物"]),
        ("evidence_chunks", ["evidence_chunks", "证据块", "chunk"]),
    ]
    l2 = [
        ("事件", ["事件"]),
        ("人物与动机", ["人物与动机", "人物动机", "动机"]),
        ("冲突", ["冲突"]),
        ("情节因果", ["情节因果", "因果"]),
        ("伏笔/回收", ["伏笔/回收", "伏笔", "回收"]),
        ("设定/地点/势力", ["设定/地点/势力", "设定", "地点", "势力"]),
        ("时间线", ["时间线"]),
        ("写作观察", ["写作观察"]),
        ("证据块", ["证据块", "evidence_chunks", "chunk"]),
    ]
    l3 = [
        ("scene_breakdown", ["scene_breakdown", "场景拆解"]),
        ("style_observation", ["style_observation", "风格观察"]),
        ("character_state", ["character_state", "人物状态"]),
        ("continuity_constraints", ["continuity_constraints", "连续性约束", "一致性约束"]),
    ]
    if level == LEVEL_L1:
        return l1
    if level == LEVEL_L2:
        return l2
    return l2 + l3


def validate_note(text: str, level: str, chapter_chunk_ids: list[str]) -> dict[str, Any]:
    quality: dict[str, Any] = {"ok": True, "level": level, "errors": []}
    if not text.strip():
        quality["errors"].append("empty")
    min_chars = note_schema(level)["min_chars"]
    if compact_len(text) < min_chars:
        quality["errors"].append("too_short")
    missing = [name for name, aliases in field_aliases(level) if not contains_any(text, aliases)]
    if missing:
        quality["missing_fields"] = missing
        quality["errors"].append("missing_fields")
    refs = sorted({chunk_id for chunk_id in chapter_chunk_ids if chunk_id in text})
    if level == LEVEL_L1:
        required_refs = 1
    elif level == LEVEL_L2:
        required_refs = min(3, len(chapter_chunk_ids))
    else:
        required_refs = max(1, math.ceil(len(chapter_chunk_ids) * 0.6))
    if len(refs) < required_refs:
        quality["missing_chunk_refs"] = {
            "required": required_refs,
            "found": refs,
        }
        quality["errors"].append("missing_chunk_refs")
    quality["referenced_chunks"] = refs
    quality["ok"] = not quality["errors"]
    return quality


def write_legacy_summary(root: Path, book_id: str, chapter: int, text: str) -> Path:
    target = book_dir(root, book_id)
    path = target / "summaries" / f"chapter-{chapter:04d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    con = sqlite3.connect(target / "index.sqlite")
    try:
        con.execute(
            """
            INSERT INTO summaries (chapter_index, summary_path, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chapter_index) DO UPDATE SET
              summary_path = excluded.summary_path,
              updated_at = excluded.updated_at
            """,
            (chapter, str(path), now_iso()),
        )
        con.commit()
    finally:
        con.close()
    return path


def submit_note(root: Path, session_id: str, chapter: int, text: str) -> dict[str, Any]:
    session = get_session(root, session_id)
    con = open_reading_db(root)
    try:
        row = con.execute(
            "SELECT * FROM chapter_read_logs WHERE session_id = ? AND chapter_index = ?",
            (session_id, chapter),
        ).fetchone()
        if not row:
            raise ValueError(f"Chapter {chapter} is not in this reading session.")
        if row["status"] not in {"issued", "completed"} and not row["manual_override"]:
            raise ValueError(f"Chapter {chapter} was not issued by read-next.")
        chunk_ids = json.loads(row["chunk_ids"])
        quality = validate_note(text, row["required_level"], chunk_ids)
        if not quality["ok"]:
            return {
                "ok": False,
                "session_id": session_id,
                "chapter": chapter,
                "required_level": row["required_level"],
                "quality": quality,
            }
        summary_path = write_legacy_summary(root, session["book_id"], chapter, text)
        stamp = now_iso()
        con.execute(
            """
            UPDATE chapter_read_logs
            SET coverage_level = ?, status = 'completed', summary_path = ?,
                quality_json = ?, updated_at = ?
            WHERE session_id = ? AND chapter_index = ?
            """,
            (row["required_level"], str(summary_path), json.dumps(quality, ensure_ascii=False), stamp, session_id, chapter),
        )
        remaining = con.execute(
            """
            SELECT chapter_index FROM chapter_read_logs
            WHERE session_id = ? AND
              CASE coverage_level
                WHEN 'NONE' THEN 0
                WHEN 'L1_SKIMMED' THEN 1
                WHEN 'L2_READ' THEN 2
                WHEN 'L3_DEEP_READ' THEN 3
                ELSE 0
              END <
              CASE required_level
                WHEN 'NONE' THEN 0
                WHEN 'L1_SKIMMED' THEN 1
                WHEN 'L2_READ' THEN 2
                WHEN 'L3_DEEP_READ' THEN 3
                ELSE 0
              END
            ORDER BY chapter_index
            """,
            (session_id,),
        ).fetchall()
        current = int(remaining[0]["chapter_index"]) if remaining else chapter
        con.execute(
            "UPDATE reading_sessions SET current_chapter = ?, status = ?, updated_at = ? WHERE session_id = ?",
            (current, "active" if remaining else "ready_to_finalize", stamp, session_id),
        )
        con.commit()
    finally:
        con.close()
    return {
        "ok": True,
        "session_id": session_id,
        "chapter": chapter,
        "coverage_level": row["required_level"],
        "summary_path": str(summary_path),
        "quality": quality,
    }


def calculate_status(root: Path, session_id: str) -> dict[str, Any]:
    session = get_session(root, session_id)
    logs = session_logs(root, session_id)
    total = len(logs)
    l1 = [row for row in logs if LEVEL_ORDER[row["coverage_level"]] >= LEVEL_ORDER[LEVEL_L1]]
    l2 = [row for row in logs if LEVEL_ORDER[row["coverage_level"]] >= LEVEL_ORDER[LEVEL_L2]]
    l3 = [row for row in logs if LEVEL_ORDER[row["coverage_level"]] >= LEVEL_ORDER[LEVEL_L3]]
    missing = [
        int(row["chapter_index"])
        for row in logs
        if LEVEL_ORDER[row["coverage_level"]] < LEVEL_ORDER[row["required_level"]]
    ]
    remaining_required = [
        {
            "chapter": int(row["chapter_index"]),
            "required_level": row["required_level"],
            "coverage_level": row["coverage_level"],
        }
        for row in logs
        if LEVEL_ORDER[row["coverage_level"]] < LEVEL_ORDER[row["required_level"]]
    ]
    required_complete = not missing
    finalized = session["status"] == "finalized"
    full_scope_allowed = required_complete and finalized
    return {
        "ok": True,
        "session_id": session_id,
        "book_id": session["book_id"],
        "goal": session["goal"],
        "mode": session["mode"],
        "status": session["status"],
        "total_chapters": total,
        "completed_chapters": total - len(missing),
        "coverage_percent": round((total - len(missing)) * 100 / max(total, 1), 2),
        "l1_coverage_percent": round(len(l1) * 100 / max(total, 1), 2),
        "l2_coverage_percent": round(len(l2) * 100 / max(total, 1), 2),
        "l3_coverage_percent": round(len(l3) * 100 / max(total, 1), 2),
        "current_chapter": session["current_chapter"],
        "missing_chapters": missing,
        "key_chapters": json.loads(session["key_chapters"]),
        "remaining_required_chapters": remaining_required,
        "required_coverage_complete": required_complete,
        "finalized": finalized,
        "full_scope_allowed": full_scope_allowed,
        "final_reports_allowed": full_scope_allowed,
    }


def finalize_session(root: Path, session_id: str) -> dict[str, Any]:
    status = calculate_status(root, session_id)
    if not status["required_coverage_complete"]:
        return {
            "ok": False,
            "session_id": session_id,
            "final_reports_allowed": False,
            "required_coverage_complete": False,
            "finalized": False,
            "full_scope_allowed": False,
            "coverage_percent": status["coverage_percent"],
            "missing_chapters": status["missing_chapters"],
            "next_step": f"novel-reader read-next {session_id} --json",
        }
    con = open_reading_db(root)
    try:
        con.execute(
            "UPDATE reading_sessions SET status = 'finalized', finalized_at = ?, updated_at = ? WHERE session_id = ?",
            (now_iso(), now_iso(), session_id),
        )
        con.commit()
    finally:
        con.close()
    status["status"] = "finalized"
    status["finalized"] = True
    status["full_scope_allowed"] = True
    status["final_reports_allowed"] = True
    return status


def scope_status_for_book(root: Path, book_id: str) -> dict[str, Any] | None:
    session = find_latest_session(root, book_id)
    if not session:
        return None
    return calculate_status(root, session["session_id"])


def full_scope_guard(
    root: Path,
    book_id: str,
    report_type: str,
    anchor_chapter: int | None = None,
    allow_unfinalized: bool = False,
    session_id: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    if session_id:
        try:
            status = calculate_status(root, session_id)
        except ValueError:
            return False, full_scope_error(
                f"Reading session not found: {session_id}",
                None,
                "all",
                f"novel-reader read-session {book_id} --goal full --mode balanced --json",
            )
        if status["book_id"] != book_id:
            return False, full_scope_error(
                f"Reading session {session_id} belongs to book {status['book_id']}, not {book_id}.",
                status,
                "all",
                f"novel-reader read-session {book_id} --goal full --mode balanced --json",
            )
    else:
        status = scope_status_for_book(root, book_id)
    if not status:
        return False, full_scope_error(
            "No reading session found for full-scope output.",
            None,
            "all",
            f"novel-reader read-session {book_id} --goal full --mode balanced --json",
        )

    mode = status["mode"]
    missing_l1 = [item["chapter"] for item in status["remaining_required_chapters"] if item["required_level"] == LEVEL_L1]
    if report_type in {"outline", "map"} and status["l1_coverage_percent"] < 100:
        return False, blocked_payload(status, "Full outline/map requires all chapters at L1.", missing_l1 or status["missing_chapters"])

    if report_type == "analyze":
        if mode not in {"balanced", "deep"} or not status["required_coverage_complete"]:
            return False, blocked_payload(status, "Full writing analysis requires a completed balanced/deep reading session.", status["missing_chapters"])

    if report_type == "predict":
        if mode not in {"balanced", "deep"} or not status["required_coverage_complete"]:
            return False, blocked_payload(status, "Full prediction requires a completed balanced/deep reading session.", status["missing_chapters"])

    if report_type == "style":
        if mode not in {"balanced", "deep"} or status["l2_coverage_percent"] <= 0 or not status["required_coverage_complete"]:
            return False, blocked_payload(status, "Full style distillation requires completed L2/L3 sample chapters.", status["missing_chapters"])

    if report_type == "continue":
        if anchor_chapter is None:
            return False, blocked_payload(status, "Full continuation requires an anchor chapter.", status["missing_chapters"])
        logs = session_logs(root, status["session_id"])
        needed = [chapter for chapter in (anchor_chapter - 1, anchor_chapter, anchor_chapter + 1) if chapter >= 1]
        bad = [
            int(row["chapter_index"])
            for row in logs
            if int(row["chapter_index"]) in needed and LEVEL_ORDER[row["coverage_level"]] < LEVEL_ORDER[LEVEL_L2]
        ]
        if bad:
            return False, blocked_payload(status, "Full continuation requires anchor-near chapters at L2 or L3.", bad)

    if not allow_unfinalized and not status["finalized"]:
        return False, blocked_payload(
            status,
            "阅读覆盖已达标，但尚未 finalize-reading。请先运行 finalize-reading。",
            [],
            f"novel-reader finalize-reading {status['session_id']} --json",
        )

    return True, {"ok": True, "reading_status": status}


def blocked_payload(status: dict[str, Any], reason: str, missing: Any, next_action: str | None = None) -> dict[str, Any]:
    return full_scope_error(
        reason,
        status,
        missing,
        next_action or f"novel-reader read-next {status['session_id']} --json",
    )


def full_scope_error(message: str, status: dict[str, Any] | None, missing: Any, next_action: str) -> dict[str, Any]:
    coverage = {
        "coverage_percent": 0,
        "l1_coverage_percent": 0,
        "l2_coverage_percent": 0,
        "l3_coverage_percent": 0,
        "session_status": None,
    }
    if status:
        coverage = {
            "coverage_percent": status["coverage_percent"],
            "l1_coverage_percent": status["l1_coverage_percent"],
            "l2_coverage_percent": status["l2_coverage_percent"],
            "l3_coverage_percent": status["l3_coverage_percent"],
            "session_status": status["status"],
        }
    return {
        "ok": False,
        "reason": message,
        "missing_chapters": missing,
        "next_step": next_action,
        "error": {
            "code": "FULL_SCOPE_NOT_ALLOWED",
            "message": message,
            "coverage": coverage,
            "missing_chapters": missing,
            "next_action": next_action,
        },
    }
