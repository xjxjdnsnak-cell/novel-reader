from __future__ import annotations

import argparse
import bisect
import contextlib
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import textwrap
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .intent_router import IntentResult, classify_request
from .reading_session import (
    build_read_next,
    calculate_status,
    create_session,
    finalize_session,
    full_scope_guard,
    submit_note,
)


DEFAULT_CHUNK_CHARS = 6000
DEFAULT_OVERLAP_CHARS = 400
SCHEMA_VERSION = 1
STYLE_SCENES = ("战斗", "悬疑", "感情", "日常", "说明")
CONTINUATION_LENGTHS = {
    "short": "约 800-1200 中文字",
    "medium": "约 1500-2500 中文字",
    "long": "约 3000-5000 中文字",
}
STYLE_SCENE_KEYWORDS = {
    "战斗": ("打", "杀", "剑", "刀", "枪", "拳", "血", "冲", "斩", "攻", "退", "闪", "吼", "痛", "伤", "爆", "轰", "敌", "战"),
    "悬疑": ("疑", "谜", "秘密", "线索", "尸", "血迹", "失踪", "黑暗", "门后", "发现", "真相", "证据", "不对", "奇怪", "阴影"),
    "感情": ("心", "泪", "笑", "爱", "喜欢", "恨", "抱", "吻", "沉默", "温柔", "想念", "疼", "舍不得", "眼眶", "颤"),
    "日常": ("吃", "饭", "茶", "街", "屋", "房", "早晨", "晚上", "买", "走", "坐", "睡", "闲聊", "厨房", "衣"),
    "说明": ("因为", "所以", "规则", "设定", "能力", "系统", "世界", "历史", "组织", "等级", "解释", "意味着", "按照", "必须", "可以"),
}

CHAPTER_PATTERNS = [
    re.compile(r"^\s{0,3}#{1,6}\s+(.{1,120}?)\s*$"),
    re.compile(r"^\s*(第[0-9０-９一二三四五六七八九十百千万萬零〇两兩]+[章节回卷部集篇].{0,80})\s*$"),
    re.compile(r"^\s*((序章|楔子|尾声|終章|终章|后记|後記|番外).{0,80})\s*$"),
    re.compile(r"^\s*((chapter|section|part)\s+[0-9ivxlcdm]+.{0,80})\s*$", re.I),
]


class NovelReaderError(RuntimeError):
    pass


class NovelReaderJsonError(RuntimeError):
    def __init__(self, payload: dict[str, Any], return_code: int = 1):
        super().__init__(json.dumps(payload, ensure_ascii=False))
        self.payload = payload
        self.return_code = return_code


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def scope_value(args: argparse.Namespace) -> str:
    return getattr(args, "scope", "partial") or "partial"


def require_full_scope(root: Path, book_id: str, report_type: str, args: argparse.Namespace, anchor_chapter: int | None = None) -> None:
    if scope_value(args) != "full":
        return
    ok, payload = full_scope_guard(root, book_id, report_type, anchor_chapter, getattr(args, "allow_unfinalized", False))
    if ok:
        return
    if getattr(args, "json", False):
        raise NovelReaderJsonError(payload)
    raise NovelReaderError(payload.get("error", {}).get("message") or payload.get("reason") or "Full-scope output is not allowed.")


def read_text_file(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug or "novel"


def storage_root(args: argparse.Namespace) -> Path:
    if getattr(args, "store", None):
        return Path(args.store).expanduser().resolve()
    env_root = os.environ.get("NOVEL_READER_HOME")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return (Path.cwd() / ".novel-reader").resolve()


def book_dir(root: Path, book_id: str) -> Path:
    candidate = Path(book_id)
    if candidate.exists() and candidate.is_dir():
        return candidate.resolve()
    return root / book_id


def manifest_path(root: Path, book_id: str) -> Path:
    return book_dir(root, book_id) / "manifest.json"


def load_manifest(root: Path, book_id: str) -> dict[str, Any]:
    path = manifest_path(root, book_id)
    if not path.exists():
        raise NovelReaderError(f"找不到书籍索引：{book_id}。先运行 novel-reader ingest <file>。")
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(root: Path, manifest: dict[str, Any]) -> None:
    path = manifest_path(root, manifest["book_id"])
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def db_path(root: Path, book_id: str) -> Path:
    return book_dir(root, book_id) / "index.sqlite"


def open_db(root: Path, book_id: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path(root, book_id))
    con.row_factory = sqlite3.Row
    return con


def init_db(con: sqlite3.Connection) -> bool:
    con.executescript(
        """
        DROP TABLE IF EXISTS chunks;
        DROP TABLE IF EXISTS summaries;
        DROP TABLE IF EXISTS embeddings;
        DROP TABLE IF EXISTS chunk_fts;

        CREATE TABLE chunks (
          chunk_id TEXT PRIMARY KEY,
          chapter_index INTEGER NOT NULL,
          chapter_title TEXT NOT NULL,
          chunk_index INTEGER NOT NULL,
          char_start INTEGER NOT NULL,
          char_end INTEGER NOT NULL,
          line_start INTEGER NOT NULL,
          line_end INTEGER NOT NULL,
          text TEXT NOT NULL
        );

        CREATE TABLE summaries (
          chapter_index INTEGER PRIMARY KEY,
          summary_path TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE embeddings (
          chunk_id TEXT PRIMARY KEY,
          provider TEXT NOT NULL,
          model TEXT NOT NULL,
          vector_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    try:
        con.execute(
            "CREATE VIRTUAL TABLE chunk_fts USING fts5("
            "chunk_id UNINDEXED, chapter_title, text, tokenize='unicode61')"
        )
        return True
    except sqlite3.OperationalError:
        return False


def detect_chapter_title(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 140:
        return None
    for pattern in CHAPTER_PATTERNS:
        match = pattern.match(line)
        if match:
            title = match.group(1).strip()
            return re.sub(r"\s+", " ", title)
    return None


def line_starts(text: str) -> list[int]:
    starts = [0]
    for match in re.finditer(r"\n", text):
        starts.append(match.end())
    return starts


def offset_to_line(starts: list[int], offset: int) -> int:
    return bisect.bisect_right(starts, offset)


def detect_chapters(text: str) -> list[dict[str, Any]]:
    starts = line_starts(text)
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, int, str]] = []
    cursor = 0
    for line_no, line in enumerate(lines, start=1):
        title = detect_chapter_title(line)
        if title:
            headings.append((cursor, line_no, title))
        cursor += len(line)

    if not headings:
        return [
            {
                "chapter_index": 1,
                "title": "正文",
                "char_start": 0,
                "char_end": len(text),
                "line_start": 1,
                "line_end": offset_to_line(starts, len(text)),
                "text": text,
            }
        ]

    chapters: list[dict[str, Any]] = []
    if headings[0][0] > 0 and text[: headings[0][0]].strip():
        chapters.append(
            {
                "chapter_index": len(chapters) + 1,
                "title": "正文前言",
                "char_start": 0,
                "char_end": headings[0][0],
                "line_start": 1,
                "line_end": headings[0][1] - 1,
                "text": text[: headings[0][0]],
            }
        )

    for i, (start, line_no, title) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        chapters.append(
            {
                "chapter_index": len(chapters) + 1,
                "title": title,
                "char_start": start,
                "char_end": end,
                "line_start": line_no,
                "line_end": offset_to_line(starts, end),
                "text": text[start:end],
            }
        )
    return chapters


def chunk_chapter(chapter: dict[str, Any], chunk_chars: int, overlap_chars: int, starts: list[int]) -> list[dict[str, Any]]:
    text = chapter["text"]
    if chunk_chars <= overlap_chars:
        raise NovelReaderError("--chunk-chars 必须大于 --overlap-chars。")

    chunks = []
    pos = 0
    chunk_index = 1
    while pos < len(text):
        end = min(pos + chunk_chars, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", pos, end), text.rfind("。", pos, end), text.rfind("！", pos, end), text.rfind("？", pos, end))
            if boundary > pos + chunk_chars // 2:
                end = boundary + 1

        abs_start = chapter["char_start"] + pos
        abs_end = chapter["char_start"] + end
        chunks.append(
            {
                "chunk_id": f"c{chapter['chapter_index']:04d}-{chunk_index:03d}",
                "chapter_index": chapter["chapter_index"],
                "chapter_title": chapter["title"],
                "chunk_index": chunk_index,
                "char_start": abs_start,
                "char_end": abs_end,
                "line_start": offset_to_line(starts, abs_start),
                "line_end": offset_to_line(starts, abs_end),
                "text": text[pos:end],
            }
        )
        if end >= len(text):
            break
        pos = max(end - overlap_chars, pos + 1)
        chunk_index += 1
    return chunks


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def command_ingest(args: argparse.Namespace) -> int:
    source = Path(args.file).expanduser().resolve()
    if not source.exists():
        raise NovelReaderError(f"文件不存在：{source}")
    if source.suffix.lower() not in {".txt", ".md", ".markdown"}:
        raise NovelReaderError("第一版只支持 TXT/Markdown：.txt、.md、.markdown。")

    root = storage_root(args)
    root.mkdir(parents=True, exist_ok=True)
    text, encoding = read_text_file(source)
    digest = sha256_file(source)
    title = args.title or source.stem
    book_id = args.book_id or f"{slugify(title)}-{digest[:10]}"
    target = book_dir(root, book_id)
    if target.exists() and not args.force:
        raise NovelReaderError(f"索引已存在：{book_id}。如需重建，加 --force。")
    if target.exists() and args.force:
        shutil.rmtree(target)

    for subdir in ("summaries", "maps", "reports", "styles", "continuations"):
        (target / subdir).mkdir(parents=True, exist_ok=True)

    starts = line_starts(text)
    chapters = detect_chapters(text)
    chunks: list[dict[str, Any]] = []
    for chapter in chapters:
        chunks.extend(chunk_chapter(chapter, args.chunk_chars, args.overlap_chars, starts))

    con = sqlite3.connect(target / "index.sqlite")
    fts_enabled = init_db(con)
    con.executemany(
        """
        INSERT INTO chunks (
          chunk_id, chapter_index, chapter_title, chunk_index,
          char_start, char_end, line_start, line_end, text
        ) VALUES (
          :chunk_id, :chapter_index, :chapter_title, :chunk_index,
          :char_start, :char_end, :line_start, :line_end, :text
        )
        """,
        chunks,
    )
    if fts_enabled:
        con.executemany(
            "INSERT INTO chunk_fts (chunk_id, chapter_title, text) VALUES (:chunk_id, :chapter_title, :text)",
            chunks,
        )
    con.commit()
    con.close()

    write_jsonl(target / "chunks.jsonl", chunks)
    write_jsonl(
        target / "chapters.jsonl",
        (
            {
                "chapter_index": c["chapter_index"],
                "title": c["title"],
                "char_start": c["char_start"],
                "char_end": c["char_end"],
                "line_start": c["line_start"],
                "line_end": c["line_end"],
            }
            for c in chapters
        ),
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "book_id": book_id,
        "title": title,
        "source_path": str(source),
        "source_sha256": digest,
        "source_size_bytes": source.stat().st_size,
        "source_encoding": encoding,
        "imported_at": now_iso(),
        "total_chars": len(text),
        "chapter_count": len(chapters),
        "chunk_count": len(chunks),
        "chunk_chars": args.chunk_chars,
        "overlap_chars": args.overlap_chars,
        "fts_enabled": fts_enabled,
        "embedding": {
            "enabled": False,
            "provider": None,
            "model": None,
            "chunk_count": 0,
            "updated_at": None,
        },
    }
    save_manifest(root, manifest)

    print_json(
        {
            "ok": True,
            "book_id": book_id,
            "title": title,
            "chapters": len(chapters),
            "chunks": len(chunks),
            "chars": len(text),
            "store": str(target),
            "fts_enabled": fts_enabled,
        }
    )
    return 0


def fetch_chapters(root: Path, book_id: str) -> list[dict[str, Any]]:
    path = book_dir(root, book_id) / "chapters.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def fetch_summary_rows(root: Path, book_id: str) -> dict[int, sqlite3.Row]:
    con = open_db(root, book_id)
    try:
        return {int(row["chapter_index"]): row for row in con.execute("SELECT * FROM summaries")}
    finally:
        con.close()


def command_status(args: argparse.Namespace) -> int:
    root = storage_root(args)
    manifest = load_manifest(root, args.book)
    summaries = fetch_summary_rows(root, args.book)
    summary_count = len(summaries)
    chapter_count = int(manifest["chapter_count"])
    unread = [i for i in range(1, chapter_count + 1) if i not in summaries]
    result = {
        "book_id": manifest["book_id"],
        "title": manifest["title"],
        "chapters": chapter_count,
        "chunks": manifest["chunk_count"],
        "chars": manifest["total_chars"],
        "summary_coverage": {
            "summarized_chapters": summary_count,
            "percent": round(summary_count * 100 / max(chapter_count, 1), 2),
            "next_unread_chapters": unread[:20],
        },
        "index": {
            "fts_enabled": manifest.get("fts_enabled", False),
            "embedding": manifest.get("embedding", {}),
            "vector_backend": "sqlite_cosine",
        },
        "store": str(book_dir(root, args.book)),
    }
    print_json(result) if args.json else print_status(result)
    return 0


def print_status(result: dict[str, Any]) -> None:
    coverage = result["summary_coverage"]
    embedding = result["index"]["embedding"]
    lines = [
        f"书籍：{result['title']} ({result['book_id']})",
        f"规模：{result['chapters']} 章 / {result['chunks']} 块 / {result['chars']} 字符",
        f"摘要覆盖：{coverage['summarized_chapters']} 章，{coverage['percent']}%",
        f"FTS 索引：{'可用' if result['index']['fts_enabled'] else '不可用，使用 LIKE fallback'}",
        f"Embedding：{'已启用' if embedding.get('enabled') else '未启用'}",
        f"存储：{result['store']}",
    ]
    if coverage["next_unread_chapters"]:
        lines.append("下批未读章节：" + ", ".join(map(str, coverage["next_unread_chapters"])))
    print("\n".join(lines))


def get_chunks(
    root: Path,
    book_id: str,
    chapter: int | None = None,
    chunk_id: str | None = None,
    limit_chars: int | None = None,
) -> list[sqlite3.Row]:
    con = open_db(root, book_id)
    try:
        if chunk_id:
            rows = list(con.execute("SELECT * FROM chunks WHERE chunk_id = ? ORDER BY chunk_index", (chunk_id,)))
        elif chapter:
            rows = list(con.execute("SELECT * FROM chunks WHERE chapter_index = ? ORDER BY chunk_index", (chapter,)))
        else:
            rows = list(con.execute("SELECT * FROM chunks ORDER BY chapter_index, chunk_index"))
        if limit_chars:
            picked = []
            total = 0
            for row in rows:
                if total >= limit_chars:
                    break
                picked.append(row)
                total += len(row["text"])
            return picked
        return rows
    finally:
        con.close()


def command_read(args: argparse.Namespace) -> int:
    root = storage_root(args)
    load_manifest(root, args.book)
    rows = get_chunks(root, args.book, chapter=args.chapter, chunk_id=args.chunk, limit_chars=args.limit_chars)
    if not rows:
        raise NovelReaderError("没有找到匹配章节或块。")
    if args.json:
        print_json([dict(row) for row in rows])
        return 0

    for row in rows:
        print(f"\n--- {row['chunk_id']} | 第 {row['chapter_index']} 章：{row['chapter_title']} | 行 {row['line_start']}-{row['line_end']} ---\n")
        print(row["text"].strip())
    return 0


def split_terms(query: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", query)
    normalized = re.sub(
        r"(为什么|為什麼|是什么|是什麼|在哪里|在哪裡|怎么|怎麼|如何|是否|是不是|谁|誰|什么|什麼|哪[个個]|原因|结果|結果)",
        " ",
        normalized,
    )
    parts = [
        part.strip()
        for part in re.split(r"[\s,，。！？!?；;：:、（）()《》<>「」『』“”\"'`]+", normalized)
        if part.strip()
    ]
    stopwords = {"的", "了", "呢", "吗", "嗎", "吧", "啊", "呀", "以及", "和", "与", "與"}
    terms: list[str] = []
    for part in parts:
        if part in stopwords:
            continue
        if len(part) >= 2:
            terms.append(part)
    if not terms and query.strip():
        terms.append(query.strip())
    return list(dict.fromkeys(terms))


def snippet(text: str, terms: list[str], width: int) -> str:
    lower = text.lower()
    pos = -1
    for term in terms:
        pos = lower.find(term.lower())
        if pos >= 0:
            break
    if pos < 0:
        pos = 0
    start = max(pos - width // 2, 0)
    end = min(start + width, len(text))
    start = max(end - width, 0)
    excerpt = text[start:end].replace("\n", " ")
    return re.sub(r"\s+", " ", excerpt).strip()


def like_search(con: sqlite3.Connection, query: str, top: int, context_chars: int) -> list[dict[str, Any]]:
    terms = split_terms(query) or [query]
    rows = list(con.execute("SELECT * FROM chunks"))
    results = []
    for row in rows:
        text_lower = row["text"].lower()
        title_lower = row["chapter_title"].lower()
        score = 0
        for term in terms:
            term_lower = term.lower()
            score += text_lower.count(term_lower) * 3
            score += title_lower.count(term_lower)
        if query and query.lower() in text_lower:
            score += 8
        if score > 0:
            results.append(
                {
                    "chunk_id": row["chunk_id"],
                    "chapter_index": row["chapter_index"],
                    "chapter_title": row["chapter_title"],
                    "line_start": row["line_start"],
                    "line_end": row["line_end"],
                    "score": score,
                    "source": "like",
                    "snippet": snippet(row["text"], terms, context_chars),
                }
            )
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:top]


def fts_query(query: str) -> str:
    terms = split_terms(query)
    if not terms:
        return '""'
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)


def fts_search(con: sqlite3.Connection, query: str, top: int, context_chars: int) -> list[dict[str, Any]]:
    try:
        rows = list(
            con.execute(
                """
                SELECT c.*, bm25(chunk_fts) AS rank
                FROM chunk_fts
                JOIN chunks c ON c.chunk_id = chunk_fts.chunk_id
                WHERE chunk_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query(query), top),
            )
        )
    except sqlite3.Error:
        return []
    terms = split_terms(query) or [query]
    return [
        {
            "chunk_id": row["chunk_id"],
            "chapter_index": row["chapter_index"],
            "chapter_title": row["chapter_title"],
            "line_start": row["line_start"],
            "line_end": row["line_end"],
            "score": float(row["rank"]),
            "source": "fts",
            "snippet": snippet(row["text"], terms, context_chars),
        }
        for row in rows
    ]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def local_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".novel-reader-local" / "config.json"


def read_local_launcher_config() -> dict[str, Any]:
    path = local_config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def local_qwen_embedding_health(port: int = 8081) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("ok") and data.get("model_loaded"):
            data["port"] = port
            return data
        return None
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def local_qwen_embedding_available(port: int = 8081) -> bool:
    return local_qwen_embedding_health(port) is not None


def discover_local_qwen_embedding() -> dict[str, Any] | None:
    ports: list[int] = []
    config = read_local_launcher_config()
    if config.get("port"):
        try:
            ports.append(int(config["port"]))
        except (TypeError, ValueError):
            pass
    base_url = os.environ.get("NOVEL_READER_EMBED_BASE_URL", "")
    match = re.search(r":(\d+)(?:/|$)", base_url)
    if match:
        ports.append(int(match.group(1)))
    ports.extend(range(8081, 8086))

    seen = set()
    for port in ports:
        if port in seen:
            continue
        seen.add(port)
        health = local_qwen_embedding_health(port)
        if health:
            return health
    return None


def resolve_embedding_config(model: str | None = None) -> tuple[str, str, str]:
    base_url = os.environ.get("NOVEL_READER_EMBED_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("NOVEL_READER_EMBED_API_KEY", "")
    resolved_model = model or os.environ.get("NOVEL_READER_EMBED_MODEL", "")

    local_health = discover_local_qwen_embedding()
    if not base_url and local_health:
        base_url = f"http://127.0.0.1:{local_health.get('port', 8081)}/v1"
        api_key = api_key or "local"
        resolved_model = resolved_model or local_health.get("model") or "qwen3-embedding-0.6b"

    if not base_url:
        base_url = "https://api.openai.com/v1"
    if not resolved_model:
        resolved_model = "text-embedding-3-small"
    if not api_key and (base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost")):
        api_key = "local"

    return api_key, base_url, resolved_model


def embed_texts(texts: list[str], provider: str, model: str) -> list[list[float]]:
    if provider != "openai-compatible":
        raise NovelReaderError("目前只实现 openai-compatible embedding provider。")
    api_key, base_url, model = resolve_embedding_config(model)
    if not api_key:
        raise NovelReaderError("未配置 NOVEL_READER_EMBED_API_KEY，无法启用 embedding。")
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/embeddings",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise NovelReaderError(f"embedding 请求失败：{exc}") from exc
    return [item["embedding"] for item in data["data"]]


def semantic_search(
    root: Path,
    book_id: str,
    query: str,
    provider: str,
    model: str,
    top: int,
    context_chars: int,
) -> list[dict[str, Any]]:
    query_vector = embed_texts([query], provider, model)[0]
    con = open_db(root, book_id)
    try:
        rows = list(
            con.execute(
                """
                SELECT e.vector_json, c.*
                FROM embeddings e
                JOIN chunks c ON c.chunk_id = e.chunk_id
                WHERE e.provider = ? AND e.model = ?
                """,
                (provider, model),
            )
        )
        scored = []
        terms = split_terms(query) or [query]
        for row in rows:
            vector = json.loads(row["vector_json"])
            scored.append(
                {
                    "chunk_id": row["chunk_id"],
                    "chapter_index": row["chapter_index"],
                    "chapter_title": row["chapter_title"],
                    "line_start": row["line_start"],
                    "line_end": row["line_end"],
                    "score": round(cosine(query_vector, vector), 6),
                    "source": "embedding",
                    "snippet": snippet(row["text"], terms, context_chars),
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top]
    finally:
        con.close()


def search_book(
    root: Path,
    book_id: str,
    query: str,
    top: int,
    context_chars: int,
    semantic: bool = False,
) -> list[dict[str, Any]]:
    manifest = load_manifest(root, book_id)
    if semantic and manifest.get("embedding", {}).get("enabled"):
        emb = manifest["embedding"]
        return semantic_search(root, book_id, query, emb["provider"], emb["model"], top, context_chars)

    con = open_db(root, book_id)
    try:
        results = []
        if manifest.get("fts_enabled"):
            results.extend(fts_search(con, query, top, context_chars))
        results.extend(like_search(con, query, top * 2, context_chars))

        deduped: dict[str, dict[str, Any]] = {}
        for item in results:
            existing = deduped.get(item["chunk_id"])
            if not existing:
                deduped[item["chunk_id"]] = item
            elif existing["source"] == "fts" and item["source"] == "like":
                item["source"] = "fts+like"
                item["score"] = max(float(existing["score"]), float(item["score"]))
                deduped[item["chunk_id"]] = item
        merged = list(deduped.values())
        merged.sort(key=lambda item: (item["source"] != "like", float(item["score"])), reverse=True)
        return merged[:top]
    finally:
        con.close()


def command_search(args: argparse.Namespace) -> int:
    root = storage_root(args)
    results = search_book(root, args.book, args.query, args.top, args.context_chars, args.semantic)
    if args.json:
        print_json(results)
        return 0
    if not results:
        print("没有找到匹配片段。可以换关键词，或在启用 embedding 后使用 --semantic。")
        return 0
    for item in results:
        print(
            f"\n[{item['source']}] {item['chunk_id']} | 第 {item['chapter_index']} 章："
            f"{item['chapter_title']} | 行 {item['line_start']}-{item['line_end']} | score={item['score']}"
        )
        print(item["snippet"])
    return 0


def command_ask(args: argparse.Namespace) -> int:
    root = storage_root(args)
    manifest = load_manifest(root, args.book)
    summaries = fetch_summary_rows(root, args.book)
    coverage = round(len(summaries) * 100 / max(int(manifest["chapter_count"]), 1), 2)
    results = search_book(root, args.book, args.question, args.top, args.context_chars, args.semantic)
    packet = {
        "question": args.question,
        "book_id": args.book,
        "title": manifest["title"],
        "summary_coverage_percent": coverage,
        "answering_policy": [
            "先根据证据片段回答，不要只凭泛化记忆。",
            "如果证据不足，明确说证据不足，并列出还需要读取的章节或关键词。",
            "回答必须引用 chunk_id、章节号和行号。",
        ],
        "evidence": results,
    }
    print_json(packet) if args.json else print_qa_packet(packet)
    return 0


def print_qa_packet(packet: dict[str, Any]) -> None:
    print(f"问题：{packet['question']}")
    print(f"书籍：{packet['title']} ({packet['book_id']})")
    print(f"摘要覆盖：{packet['summary_coverage_percent']}%")
    print("\n回答要求：")
    for rule in packet["answering_policy"]:
        print(f"- {rule}")
    print("\n证据片段：")
    if not packet["evidence"]:
        print("- 未检索到证据。")
    for item in packet["evidence"]:
        print(
            f"\n- {item['chunk_id']} | 第 {item['chapter_index']} 章：{item['chapter_title']} "
            f"| 行 {item['line_start']}-{item['line_end']}"
        )
        print(f"  {item['snippet']}")


def summary_path_for(book: Path, chapter: int) -> Path:
    return book / "summaries" / f"chapter-{chapter:04d}.md"


def command_note(args: argparse.Namespace) -> int:
    root = storage_root(args)
    manifest = load_manifest(root, args.book)
    target = book_dir(root, args.book)
    if args.text:
        text = args.text
    elif args.file:
        text = Path(args.file).expanduser().read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    if not text.strip():
        raise NovelReaderError("摘要内容为空。请用 --text、--file 或 stdin 提供内容。")
    if args.chapter < 1 or args.chapter > int(manifest["chapter_count"]):
        raise NovelReaderError("章节号超出范围。")

    path = summary_path_for(target, args.chapter)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    con = open_db(root, args.book)
    try:
        con.execute(
            """
            INSERT INTO summaries (chapter_index, summary_path, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chapter_index) DO UPDATE SET
              summary_path = excluded.summary_path,
              updated_at = excluded.updated_at
            """,
            (args.chapter, str(path), now_iso()),
        )
        con.commit()
    finally:
        con.close()
    print_json({"ok": True, "chapter": args.chapter, "summary_path": str(path)})
    return 0


def command_read_session(args: argparse.Namespace) -> int:
    root = storage_root(args)
    result = create_session(
        root,
        args.book,
        args.goal,
        args.mode,
        args.deep_ratio,
        query=args.query,
        focus_chapter=args.focus_chapter,
        after_chapter=args.after_chapter,
    )
    print_json(result)
    return 0


def command_read_next(args: argparse.Namespace) -> int:
    root = storage_root(args)
    result = build_read_next(root, args.session_id, args.batch_chapters, args.chapter)
    print_json(result)
    return 0


def command_submit_note(args: argparse.Namespace) -> int:
    root = storage_root(args)
    if args.text is not None:
        text = args.text
    elif args.file:
        text = Path(args.file).expanduser().read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    result = submit_note(root, args.session_id, args.chapter, text)
    print_json(result)
    return 0 if result.get("ok") else 1


def command_reading_status(args: argparse.Namespace) -> int:
    root = storage_root(args)
    print_json(calculate_status(root, args.session_id))
    return 0


def command_finalize_reading(args: argparse.Namespace) -> int:
    root = storage_root(args)
    result = finalize_session(root, args.session_id)
    print_json(result)
    return 0 if result.get("ok", result.get("full_scope_allowed")) else 1


def load_summaries(book_path: Path) -> list[tuple[int, str]]:
    rows = []
    for path in sorted((book_path / "summaries").glob("chapter-*.md")):
        match = re.search(r"chapter-(\d+)\.md$", path.name)
        if match:
            rows.append((int(match.group(1)), path.read_text(encoding="utf-8").strip()))
    return rows


def first_lines(text: str, max_chars: int = 600) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1] + "…"


def render_outline(manifest: dict[str, Any], chapters: list[dict[str, Any]], summaries: list[tuple[int, str]]) -> str:
    summary_by_chapter = dict(summaries)
    lines = [
        f"# {manifest['title']} 情节梳理",
        "",
        f"- book_id: `{manifest['book_id']}`",
        f"- chapters: {manifest['chapter_count']}",
        f"- chunks: {manifest['chunk_count']}",
        f"- summary coverage: {len(summaries)}/{manifest['chapter_count']}",
        "",
        "## 分章梳理",
    ]
    for chapter in chapters:
        index = int(chapter["chapter_index"])
        title = chapter["title"]
        summary = summary_by_chapter.get(index)
        lines.append(f"### 第 {index} 章：{title}")
        if summary:
            lines.append(first_lines(summary, 900))
        else:
            lines.append("未记录摘要。请先读取本章并用 `novel-reader note` 写入结构化摘要。")
        lines.append("")

    lines.extend(
        [
            "## 主线与支线待整理",
            "- 主线：基于已读章节摘要归纳核心目标、阻力、转折和结果。",
            "- 支线：列出人物支线、感情线、势力线、成长线及其与主线的连接点。",
            "- 关键转折：标记导致目标、关系、局势发生不可逆变化的章节。",
            "- 高潮与结局：全书读完后再补全，未读完时不要强行下结论。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def command_outline(args: argparse.Namespace) -> int:
    root = storage_root(args)
    require_full_scope(root, args.book, "outline", args)
    manifest = load_manifest(root, args.book)
    target = book_dir(root, args.book)
    chapters = fetch_chapters(root, args.book)
    summaries = load_summaries(target)
    outline = render_outline(manifest, chapters, summaries)
    if args.write:
        out = target / "maps" / "outline.md"
        out.write_text(outline, encoding="utf-8")
        print_json({"ok": True, "path": str(out)})
    elif getattr(args, "json", False):
        print_json({"ok": True, "text": outline})
    else:
        print(outline)
    return 0


def render_map(manifest: dict[str, Any], summaries: list[tuple[int, str]]) -> str:
    lines = [
        f"# {manifest['title']} 全书地图",
        "",
        "这份地图由已记录的章节摘要汇总而来。人物、伏笔和设定结论需要在回答时继续回查原文证据。",
        "",
        "## 覆盖率",
        f"- 已摘要章节：{len(summaries)}/{manifest['chapter_count']}",
        f"- 总块数：{manifest['chunk_count']}",
        "",
        "## 章节证据索引",
    ]
    for chapter, summary in summaries:
        lines.append(f"- 第 {chapter} 章：{first_lines(summary, 260)}")
    lines.extend(
        [
            "",
            "## 人物表",
            "- 待模型根据章节摘要与原文证据补全：姓名、首次出现、目标、动机、关系、人物弧光。",
            "",
            "## 事件表",
            "- 待模型补全：事件、章节、原因、结果、影响范围、证据 chunk。",
            "",
            "## 时间线",
            "- 待模型补全：时间点、事件、涉及人物、顺序矛盾。",
            "",
            "## 伏笔表",
            "- 待模型补全：埋设章节、线索、回收章节、是否悬而未决。",
            "",
            "## 设定表",
            "- 待模型补全：世界观规则、能力体系、组织/势力、地点、限制与例外。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def command_map(args: argparse.Namespace) -> int:
    root = storage_root(args)
    require_full_scope(root, args.book, "map", args)
    manifest = load_manifest(root, args.book)
    target = book_dir(root, args.book)
    content = render_map(manifest, load_summaries(target))
    out = target / "maps" / "book-map.md"
    out.write_text(content, encoding="utf-8")
    print_json({"ok": True, "path": str(out)})
    return 0


def render_analysis(manifest: dict[str, Any], summaries: list[tuple[int, str]]) -> str:
    coverage = round(len(summaries) * 100 / max(int(manifest["chapter_count"]), 1), 2)
    lines = [
        f"# {manifest['title']} 写作分析报告",
        "",
        f"- 摘要覆盖：{coverage}%",
        "- 结论等级：未读完时只输出阶段性诊断；读完后再输出全书定稿诊断。",
        "",
        "## 情节结构",
        "- 主线目标是否清楚：待基于全书地图判断。",
        "- 转折是否足够：待标记关键转折章节。",
        "- 高潮与结局是否兑现承诺：全书读完后判断。",
        "",
        "## 人物弧光",
        "- 核心人物的欲望、恐惧、选择和代价需要绑定证据 chunk。",
        "",
        "## 节奏与冲突",
        "- 检查每组章节的冲突密度、信息揭示、爽点/痛点、低效重复。",
        "",
        "## 伏笔与设定一致性",
        "- 对照伏笔表与设定表，列出已回收、未回收、冲突和模糊项。",
        "",
        "## 修改建议",
        "- 按优先级输出：结构级、人物级、章节级、句段级。",
        "",
        "## 已读摘要样本",
    ]
    for chapter, summary in summaries[:20]:
        lines.append(f"- 第 {chapter} 章：{first_lines(summary, 260)}")
    if len(summaries) > 20:
        lines.append(f"- 其余 {len(summaries) - 20} 章摘要已省略，可查 summaries/。")
    return "\n".join(lines).rstrip() + "\n"


def command_analyze(args: argparse.Namespace) -> int:
    root = storage_root(args)
    require_full_scope(root, args.book, "analyze", args)
    manifest = load_manifest(root, args.book)
    target = book_dir(root, args.book)
    content = render_analysis(manifest, load_summaries(target))
    out = target / "reports" / "writing-analysis.md"
    out.write_text(content, encoding="utf-8")
    print_json({"ok": True, "path": str(out)})
    return 0


def percentile(values: list[int], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int((len(ordered) - 1) * ratio), len(ordered) - 1)
    return float(ordered[index])


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;…])", text)
    return [part.strip() for part in parts if part.strip()]


def split_paragraphs(text: str) -> list[str]:
    paragraphs = re.split(r"\n\s*\n+", text)
    return [re.sub(r"\s+", " ", part).strip() for part in paragraphs if part.strip()]


def dialogue_char_count(text: str) -> int:
    total = 0
    quote_patterns = [
        r"“([^”]{1,240})”",
        r"「([^」]{1,240})」",
        r"『([^』]{1,240})』",
        r'"([^"\n]{1,240})"',
    ]
    for pattern in quote_patterns:
        total += sum(len(match.group(1)) for match in re.finditer(pattern, text))
    dialogue_lines = [line.strip() for line in text.splitlines() if re.search(r"(：|:)\s*[“\"「『]?", line)]
    total += sum(min(len(line), 160) for line in dialogue_lines)
    return min(total, len(text))


def clean_excerpt(text: str, max_chars: int = 96) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1] + "…"


def excerpt_near_keywords(text: str, keywords: Iterable[str], max_chars: int = 96) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    lower = compact.lower()
    pos = -1
    for keyword in keywords:
        found = lower.find(keyword.lower())
        if found >= 0:
            pos = found
            break
    if pos < 0:
        return clean_excerpt(compact, max_chars)
    start = max(pos - max_chars // 2, 0)
    end = min(start + max_chars, len(compact))
    start = max(end - max_chars, 0)
    return clean_excerpt(compact[start:end], max_chars)


def cjk_ngrams(text: str, min_len: int = 2, max_len: int = 4) -> Iterable[str]:
    compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", " ", text)
    stop = {
        "他们", "她们", "我们", "你们", "自己", "这个", "那个", "什么", "一个", "没有", "不是", "还是",
        "只是", "已经", "因为", "所以", "然后", "这样", "那样", "起来", "下去", "过去", "出来",
    }
    for segment in compact.split():
        if re.fullmatch(r"[\u4e00-\u9fff]+", segment):
            limit = min(max_len, len(segment))
            for size in range(min_len, limit + 1):
                for i in range(0, len(segment) - size + 1):
                    token = segment[i : i + size]
                    if token not in stop:
                        yield token
        elif len(segment) >= 3:
            token = segment.lower()
            if token not in stop and not token.isdigit():
                yield token


def text_stats(text: str) -> dict[str, Any]:
    sentences = split_sentences(text)
    paragraphs = split_paragraphs(text)
    sentence_lengths = [len(sentence) for sentence in sentences]
    paragraph_lengths = [len(paragraph) for paragraph in paragraphs]
    punctuation = Counter(ch for ch in text if ch in "，。！？；：、“”‘’（）《》—…,.!?;:\"'()")
    chars = max(len(text), 1)
    dialogue_chars = dialogue_char_count(text)
    ngrams = Counter(cjk_ngrams(text))
    return {
        "chars": len(text),
        "sentences": len(sentences),
        "paragraphs": len(paragraphs),
        "sentence_length": {
            "avg": round(sum(sentence_lengths) / max(len(sentence_lengths), 1), 2),
            "median": round(percentile(sentence_lengths, 0.5), 2),
            "p90": round(percentile(sentence_lengths, 0.9), 2),
            "short_ratio": round(sum(1 for item in sentence_lengths if item <= 18) / max(len(sentence_lengths), 1), 3),
            "long_ratio": round(sum(1 for item in sentence_lengths if item >= 60) / max(len(sentence_lengths), 1), 3),
        },
        "paragraph_length": {
            "avg": round(sum(paragraph_lengths) / max(len(paragraph_lengths), 1), 2),
            "median": round(percentile(paragraph_lengths, 0.5), 2),
            "p90": round(percentile(paragraph_lengths, 0.9), 2),
        },
        "dialogue_ratio": round(dialogue_chars / chars, 3),
        "punctuation_per_1k": {key: round(value * 1000 / chars, 2) for key, value in punctuation.most_common(12)},
        "top_terms": [{"term": term, "count": count} for term, count in ngrams.most_common(20)],
    }


def fetch_all_chunks(root: Path, book_id: str) -> list[dict[str, Any]]:
    con = open_db(root, book_id)
    try:
        return [
            dict(row)
            for row in con.execute("SELECT * FROM chunks ORDER BY chapter_index, chunk_index")
        ]
    finally:
        con.close()


def chunk_scene_score(text: str, scene: str) -> int:
    keywords = STYLE_SCENE_KEYWORDS[scene]
    return sum(text.count(keyword) for keyword in keywords)


def pick_style_evidence(chunks: list[dict[str, Any]], scene: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
    if scene:
        keywords = STYLE_SCENE_KEYWORDS[scene]
        scored = [
            (chunk_scene_score(chunk["text"], scene), index, chunk)
            for index, chunk in enumerate(chunks)
        ]
        scored = [item for item in scored if item[0] > 0]
        scored.sort(key=lambda item: (-item[0], item[1]))
        picked = [chunk for _, _, chunk in scored[:limit]]
    else:
        if not chunks:
            picked = []
        elif len(chunks) <= limit:
            picked = chunks
        else:
            indexes = sorted({round(i * (len(chunks) - 1) / (limit - 1)) for i in range(limit)})
            picked = [chunks[index] for index in indexes]
        keywords = []

    evidence = []
    for chunk in picked:
        evidence.append(
            {
                "chunk_id": chunk["chunk_id"],
                "chapter_index": chunk["chapter_index"],
                "chapter_title": chunk["chapter_title"],
                "line_start": chunk["line_start"],
                "line_end": chunk["line_end"],
                "excerpt": excerpt_near_keywords(chunk["text"], keywords),
            }
        )
    return evidence


def build_scene_profiles(chunks: list[dict[str, Any]], target_scene: str | None = None) -> list[dict[str, Any]]:
    scenes = [target_scene] if target_scene else list(STYLE_SCENES)
    profiles = []
    for scene in scenes:
        scored = [
            (chunk_scene_score(chunk["text"], scene), chunk)
            for chunk in chunks
        ]
        selected = [chunk for score, chunk in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0][:12]
        text = "\n\n".join(chunk["text"] for chunk in selected)
        profiles.append(
            {
                "scene": scene,
                "matched_chunks": len(selected),
                "keywords": list(STYLE_SCENE_KEYWORDS[scene]),
                "stats": text_stats(text) if text else text_stats(""),
                "evidence": pick_style_evidence(selected, scene=scene, limit=5),
            }
        )
    return profiles


def build_style_packet(root: Path, book_id: str, scene: str | None = None) -> dict[str, Any]:
    manifest = load_manifest(root, book_id)
    summaries = fetch_summary_rows(root, book_id)
    chunks = fetch_all_chunks(root, book_id)
    corpus_text = "\n\n".join(chunk["text"] for chunk in chunks)
    coverage = round(len(summaries) * 100 / max(int(manifest["chapter_count"]), 1), 2)
    return {
        "book_id": book_id,
        "title": manifest["title"],
        "style_purpose": "原创转写指南；分析可迁移技法，不生成直接仿冒特定作者的提示词。",
        "summary_coverage_percent": coverage,
        "scene": scene,
        "corpus_stats": text_stats(corpus_text),
        "whole_book_evidence": pick_style_evidence(chunks, limit=8),
        "scene_profiles": build_scene_profiles(chunks, scene),
        "model_tasks": [
            "基于统计和短引文证据提炼语言风格画像。",
            "区分可迁移技法与不可照搬的具体表达。",
            "输出原创写作指南、场景写法建议和禁用清单。",
            "重要判断引用 chunk_id、章节和行号。",
        ],
    }


def style_tendency(stats: dict[str, Any]) -> list[str]:
    sentence = stats["sentence_length"]
    paragraph = stats["paragraph_length"]
    dialogue = stats["dialogue_ratio"]
    tendencies = []
    if sentence["avg"] <= 24:
        tendencies.append("句子整体偏短，适合快节奏推进。")
    elif sentence["avg"] >= 45:
        tendencies.append("句子整体偏长，信息密度和铺陈感较强。")
    else:
        tendencies.append("句长处于中等区间，叙述与描写较均衡。")
    if paragraph["avg"] <= 90:
        tendencies.append("段落较短，阅读停顿频繁。")
    elif paragraph["avg"] >= 220:
        tendencies.append("段落较长，适合连续叙述或氛围铺展。")
    else:
        tendencies.append("段落长度适中。")
    if dialogue >= 0.22:
        tendencies.append("对白占比较高，人物互动承担较多信息传递。")
    elif dialogue <= 0.08:
        tendencies.append("对白占比较低，叙述和描写承担主要推进。")
    else:
        tendencies.append("对白与叙述比例较平衡。")
    return tendencies


def render_scene_profile(scene: dict[str, Any]) -> list[str]:
    stats = scene["stats"]
    lines = [
        f"### {scene['scene']}",
        f"- 匹配块数：{scene['matched_chunks']}",
        f"- 平均句长：{stats['sentence_length']['avg']}；段落均长：{stats['paragraph_length']['avg']}；对白比例：{stats['dialogue_ratio']}",
        "- 高频词场：" + ", ".join(item["term"] for item in stats["top_terms"][:10]),
        "- 初步倾向：" + " ".join(style_tendency(stats)),
        "- 证据：",
    ]
    if not scene["evidence"]:
        lines.append("  - 未找到足够场景证据。")
    for item in scene["evidence"]:
        lines.append(
            f"  - {item['chunk_id']} | 第 {item['chapter_index']} 章：{item['chapter_title']} "
            f"| 行 {item['line_start']}-{item['line_end']} | 「{item['excerpt']}」"
        )
    return lines


def render_style_profile(packet: dict[str, Any]) -> str:
    stats = packet["corpus_stats"]
    lines = [
        f"# {packet['title']} 语言风格蒸馏",
        "",
        f"- book_id: `{packet['book_id']}`",
        f"- 用途：{packet['style_purpose']}",
        f"- 摘要覆盖：{packet['summary_coverage_percent']}%",
        "",
        "## 全书统计画像",
        f"- 字符数：{stats['chars']}；句子数：{stats['sentences']}；段落数：{stats['paragraphs']}",
        f"- 句长：平均 {stats['sentence_length']['avg']}，中位 {stats['sentence_length']['median']}，P90 {stats['sentence_length']['p90']}",
        f"- 段长：平均 {stats['paragraph_length']['avg']}，中位 {stats['paragraph_length']['median']}，P90 {stats['paragraph_length']['p90']}",
        f"- 对白比例：{stats['dialogue_ratio']}",
        "- 高频词场：" + ", ".join(item["term"] for item in stats["top_terms"][:20]),
        "- 标点密度：" + ", ".join(f"{key}:{value}/千字" for key, value in stats["punctuation_per_1k"].items()),
        "",
        "## 初步风格倾向",
    ]
    lines.extend(f"- {item}" for item in style_tendency(stats))
    lines.extend(
        [
            "",
            "## 全书代表性短引文",
        ]
    )
    for item in packet["whole_book_evidence"]:
        lines.append(
            f"- {item['chunk_id']} | 第 {item['chapter_index']} 章：{item['chapter_title']} "
            f"| 行 {item['line_start']}-{item['line_end']} | 「{item['excerpt']}」"
        )
    lines.extend(["", "## 场景类型风格"])
    for scene in packet["scene_profiles"]:
        lines.extend(render_scene_profile(scene))
        lines.append("")
    lines.extend(
        [
            "## 原创转写指南",
            "- 只迁移节奏、视角、信息释放、意象组织、对白功能等抽象技法。",
            "- 不复制原文句子、专名、独特比喻、连续措辞或可识别段落结构。",
            "- 写新文本时先确定场景类型，再选择对应的句长、段落、对白和意象策略。",
            "- 生成文本后用本报告反查：是否过度贴近原文表达，是否保留了原创设定和新人物声线。",
            "",
            "## 禁用清单",
            "- 禁止输出“写得像某作者”的直接仿写提示词。",
            "- 禁止复用报告中的短引文作为新文本正文。",
            "- 禁止把统计倾向当作固定公式；需要服务新故事的题材、人物和节奏。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_scene_styles(packet: dict[str, Any]) -> str:
    lines = [
        f"# {packet['title']} 场景类型风格指南",
        "",
        "以下内容用于原创写作迁移，只保留抽象技法与证据位置。",
        "",
    ]
    for scene in packet["scene_profiles"]:
        lines.extend(render_scene_profile(scene))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_style_guide(packet: dict[str, Any]) -> str:
    lines = [
        f"# {packet['title']} 原创转写指南",
        "",
        "## 使用方式",
        "- 选择目标场景类型。",
        "- 参考该场景的句长、段落、对白比例和意象词场。",
        "- 使用新的角色、设定、冲突和措辞完成原创文本。",
        "",
        "## 可迁移项",
        "- 叙述距离：镜头贴近人物还是偏全知说明。",
        "- 节奏控制：短句推进、长句铺陈、标点停顿。",
        "- 信息释放：先动作后解释、先氛围后真相、对话中埋线索。",
        "- 意象组织：反复出现的感官、天气、空间、身体动作类别。",
        "- 对白功能：推进冲突、隐藏动机、解释设定或制造反差。",
        "",
        "## 不可迁移项",
        "- 原文连续表达、特殊比喻、标志性句子和可识别桥段。",
        "- 原作品专有名词、人物关系、世界观设定和情节转折。",
        "- 直接声明要模仿某个在世作者或特定作品。",
    ]
    return "\n".join(lines).rstrip() + "\n"


def command_style(args: argparse.Namespace) -> int:
    if args.scene and args.scene not in STYLE_SCENES:
        raise NovelReaderError("--scene 只支持：战斗、悬疑、感情、日常、说明。")
    root = storage_root(args)
    require_full_scope(root, args.book, "style", args)
    packet = build_style_packet(root, args.book, args.scene)
    if args.json:
        print_json(packet)
        return 0

    profile = render_style_profile(packet)
    if args.write:
        target = book_dir(root, args.book)
        style_dir = target / "styles"
        style_dir.mkdir(parents=True, exist_ok=True)
        if args.scene:
            out = style_dir / f"scene-{args.scene}.md"
            out.write_text(profile, encoding="utf-8")
            print_json({"ok": True, "paths": [str(out)]})
        else:
            files = {
                "style-profile.md": profile,
                "scene-styles.md": render_scene_styles(packet),
                "style-guide.md": render_style_guide(packet),
            }
            paths = []
            for name, content in files.items():
                out = style_dir / name
                out.write_text(content, encoding="utf-8")
                paths.append(str(out))
            print_json({"ok": True, "paths": paths})
        return 0

    print(profile)
    return 0


def read_outline_arg(args: argparse.Namespace) -> str | None:
    if args.outline:
        return args.outline.strip()
    if args.outline_file:
        path = Path(args.outline_file).expanduser()
        if not path.exists():
            raise NovelReaderError(f"大纲文件不存在：{path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise NovelReaderError(f"大纲文件为空：{path}")
        return text
    return None


def chunk_excerpt(chunk: dict[str, Any], max_chars: int = 400) -> str:
    text = re.sub(r"\s+", " ", chunk["text"]).strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars + 1 :] + "…"


def find_anchor_index(
    chunks: list[dict[str, Any]],
    chapter_count: int,
    summaries: dict[int, sqlite3.Row],
    after_chapter: int | None,
    after_chunk: str | None,
    outline: str | None,
) -> tuple[int, dict[str, Any], str]:
    if after_chapter is not None:
        if after_chapter < 1 or after_chapter > chapter_count:
            raise NovelReaderError(f"--after-chapter 超出范围：{after_chapter}。本书共有 {chapter_count} 章。")
        matching = [(index, chunk) for index, chunk in enumerate(chunks) if int(chunk["chapter_index"]) == after_chapter]
        if not matching:
            raise NovelReaderError(f"第 {after_chapter} 章没有可用文本块。")
        index, chunk = matching[-1]
        return index, chunk, "after_chapter"

    if after_chunk is not None:
        for index, chunk in enumerate(chunks):
            if chunk["chunk_id"] == after_chunk:
                return index, chunk, "after_chunk"
        raise NovelReaderError(f"找不到 chunk：{after_chunk}")

    if outline:
        if summaries:
            chapter = max(summaries)
            matching = [(index, chunk) for index, chunk in enumerate(chunks) if int(chunk["chapter_index"]) == chapter]
            if matching:
                index, chunk = matching[-1]
                return index, chunk, "last_summarized_chapter"
        raise NovelReaderError("只有 --outline 时，需要已有章节摘要进度；否则请指定 --after-chapter 或 --after-chunk。")

    raise NovelReaderError("请指定 --after-chapter、--after-chunk、--outline 或 --outline-file。")


def build_recent_context(chunks: list[dict[str, Any]], anchor_index: int, context_chunks: int) -> list[dict[str, Any]]:
    start = max(0, anchor_index - context_chunks + 1)
    recent = []
    total = anchor_index - start + 1
    for offset, chunk in enumerate(chunks[start : anchor_index + 1], start=1):
        recent.append(
            {
                "chunk_id": chunk["chunk_id"],
                "chapter": chunk["chapter_index"],
                "chapter_title": chunk["chapter_title"],
                "position": f"continuation-anchor-{offset - total}",
                "line_start": chunk["line_start"],
                "line_end": chunk["line_end"],
                "excerpt": chunk_excerpt(chunk, 400),
                "summary": "续写点前文片段，用于保持事件、人物状态和叙述承接。",
            }
        )
    return recent


def continuation_query(outline: str | None, recent_context: list[dict[str, Any]]) -> str:
    if outline:
        return outline
    text = " ".join(item["excerpt"] for item in recent_context[-3:])
    terms = Counter(cjk_ngrams(text)).most_common(12)
    return " ".join(term for term, _ in terms) or text[:120]


def summarize_style_for_continue(packet: dict[str, Any], scene: str | None) -> dict[str, Any]:
    stats = packet["corpus_stats"]
    profile = {
        "purpose": "原创风格迁移；只迁移节奏、叙述距离、人物声线和设定一致性，不复制原文表达。",
        "scene": scene,
        "summary_coverage_percent": packet["summary_coverage_percent"],
        "sentence_length": stats["sentence_length"],
        "paragraph_length": stats["paragraph_length"],
        "dialogue_ratio": stats["dialogue_ratio"],
        "top_terms": stats["top_terms"][:12],
        "whole_book_evidence": packet["whole_book_evidence"][:5],
        "scene_profiles": [],
    }
    for scene_profile in packet["scene_profiles"][:2]:
        profile["scene_profiles"].append(
            {
                "scene": scene_profile["scene"],
                "matched_chunks": scene_profile["matched_chunks"],
                "stats": scene_profile["stats"],
                "evidence": scene_profile["evidence"][:3],
            }
        )
    return profile


def build_constraints(packet: dict[str, Any], recent_context: list[dict[str, Any]], plot_evidence: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]] | list[str]]:
    last = recent_context[-1] if recent_context else None
    hard = [
        {
            "type": "continuity",
            "content": "续写必须承接 recent_context 的最后事件状态，不得改写已经发生的事实。",
            "evidence": [last["chunk_id"]] if last else [],
        },
        {
            "type": "setting",
            "content": "涉及人物、地点、组织、能力、时间线时，优先遵守 plot_evidence 中能定位到的原文证据。",
            "evidence": [item["chunk_id"] for item in plot_evidence[:5]],
        },
    ]
    inferred = [
        {
            "type": "character",
            "content": "人物动机、关系和隐藏信息只按证据渐进推进；证据不足处保持留白，不突然全知。",
            "evidence": [item["chunk_id"] for item in plot_evidence[:3]],
        },
        {
            "type": "style",
            "content": "迁移叙述节奏、对白功能和场景推进方式，但使用新的措辞和新的具体表达。",
            "evidence": [item["chunk_id"] for item in packet["style_evidence"].get("whole_book_evidence", [])[:3]],
        },
    ]
    uncertain = []
    if packet["warnings"]:
        uncertain.extend({"type": "warning", "content": warning, "evidence": []} for warning in packet["warnings"])
    return {
        "hard": hard,
        "inferred": inferred,
        "uncertain": uncertain,
        "copyright_boundary": [
            "不得复制原文连续表达、标志性句式、独特比喻或可识别段落结构。",
            "不得复用原作品专有设定、桥段或人物关系作为新创作的替代品。",
            "只迁移抽象技法：节奏、叙述距离、信息释放、人物声线和场景功能。",
            "不要输出“写得像某作者”的直接仿写提示词。",
        ],
    }


def build_continuation_packet(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_manifest(root, args.book)
    chunks = fetch_all_chunks(root, args.book)
    if not chunks:
        raise NovelReaderError("本书没有可用文本块。请重新 ingest。")

    summaries = fetch_summary_rows(root, args.book)
    outline = read_outline_arg(args)
    anchor_index, anchor_chunk, mode = find_anchor_index(
        chunks,
        int(manifest["chapter_count"]),
        summaries,
        args.after_chapter,
        args.after_chunk,
        outline,
    )
    recent_context = build_recent_context(chunks, anchor_index, args.context_chunks)
    query = continuation_query(outline, recent_context)
    plot_evidence = search_book(root, args.book, query, args.evidence_top, 360, args.semantic) if query else []
    style_packet = build_style_packet(root, args.book, args.scene)
    style_evidence = summarize_style_for_continue(style_packet, args.scene)

    coverage = round(len(summaries) * 100 / max(int(manifest["chapter_count"]), 1), 2)
    warnings = []
    if coverage < 100:
        warnings.append(f"摘要覆盖率为 {coverage}%，全书级人物/伏笔/设定约束可能不完整。")
    if args.semantic and not manifest.get("embedding", {}).get("enabled"):
        warnings.append("请求了 --semantic，但本书未启用 embedding；已回退到本地 FTS/关键词检索。")
    if not plot_evidence:
        warnings.append("没有检索到额外剧情证据；续写时应更多依赖 recent_context，并降低确定性。")

    packet = {
        "ok": True,
        "schema_version": "1.0",
        "book": {
            "id": manifest["book_id"],
            "title": manifest["title"],
            "chapter_count": manifest["chapter_count"],
            "chunk_count": manifest["chunk_count"],
        },
        "continuation_goal": {
            "mode": mode if not outline else f"{mode}+outline",
            "after_chapter": args.after_chapter,
            "after_chunk": args.after_chunk,
            "anchor_chunk": anchor_chunk["chunk_id"],
            "anchor_chapter": anchor_chunk["chapter_index"],
            "anchor_chapter_title": anchor_chunk["chapter_title"],
            "outline": outline,
            "target_length": args.length,
            "target_length_hint": CONTINUATION_LENGTHS[args.length],
            "scene": args.scene,
            "query": query,
        },
        "recent_context": recent_context,
        "plot_evidence": [
            {
                "chunk_id": item["chunk_id"],
                "chapter": item["chapter_index"],
                "chapter_title": item["chapter_title"],
                "line_start": item["line_start"],
                "line_end": item["line_end"],
                "reason": "与续写位置、大纲关键词或近邻剧情相关。",
                "excerpt": item["snippet"],
                "source": item["source"],
            }
            for item in plot_evidence
        ],
        "style_evidence": style_evidence,
        "warnings": warnings,
        "draft_instructions": [
            "先阅读 recent_context，确定续写点的最后事件、人物状态和叙述视角。",
            "再阅读 plot_evidence，只把有证据的位置当作事实约束。",
            "按 continuation_goal 写新的原创正文，满足 target_length_hint。",
            "保持人物动机、时间线、设定规则和伏笔状态一致；证据不足处保持悬念或模糊处理。",
            "参考 style_evidence 的节奏和场景功能，但不要复用原文句子、独特比喻或可识别表达。",
            "正文后输出 self_checklist，逐项说明是否通过。",
        ],
        "self_checklist": [
            "剧情是否自然承接 anchor_chunk 和 recent_context？",
            "人物行为、知识范围和动机是否符合前文？",
            "设定、时间线、地点和组织关系是否与 plot_evidence 冲突？",
            "是否满足用户 outline 和目标 scene？",
            "语言节奏是否只做原创迁移，没有复制原文连续表达？",
            "是否保留未被证据确认的悬念，而不是强行解释？",
        ],
    }
    packet["constraints"] = build_constraints(packet, recent_context, packet["plot_evidence"])
    return packet


def render_continuation_pack(packet: dict[str, Any]) -> str:
    goal = packet["continuation_goal"]
    lines = [
        f"# {packet['book']['title']} 续写任务包",
        "",
        f"- book_id: `{packet['book']['id']}`",
        f"- mode: {goal['mode']}",
        f"- anchor: {goal['anchor_chunk']} / 第 {goal['anchor_chapter']} 章：{goal['anchor_chapter_title']}",
        f"- target_length: {goal['target_length']}（{goal['target_length_hint']}）",
        f"- scene: {goal['scene'] or '未指定'}",
    ]
    if goal["outline"]:
        lines.extend(["", "## 用户大纲", goal["outline"]])
    if packet["warnings"]:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in packet["warnings"])

    lines.extend(["", "## Recent Context"])
    for item in packet["recent_context"]:
        lines.append(
            f"- {item['chunk_id']} | 第 {item['chapter']} 章：{item['chapter_title']} "
            f"| 行 {item['line_start']}-{item['line_end']} | {item['excerpt']}"
        )

    lines.extend(["", "## Plot Evidence"])
    if not packet["plot_evidence"]:
        lines.append("- 未检索到额外剧情证据。")
    for item in packet["plot_evidence"]:
        lines.append(
            f"- {item['chunk_id']} | 第 {item['chapter']} 章：{item['chapter_title']} "
            f"| 行 {item['line_start']}-{item['line_end']} | {item['excerpt']}"
        )

    style = packet["style_evidence"]
    lines.extend(
        [
            "",
            "## Style Evidence",
            f"- 用途：{style['purpose']}",
            f"- 句长：平均 {style['sentence_length']['avg']}，P90 {style['sentence_length']['p90']}",
            f"- 段长：平均 {style['paragraph_length']['avg']}，P90 {style['paragraph_length']['p90']}",
            f"- 对白比例：{style['dialogue_ratio']}",
            "- 高频词场：" + ", ".join(item["term"] for item in style["top_terms"][:10]),
        ]
    )
    for scene in style["scene_profiles"]:
        lines.append(f"- 场景 {scene['scene']}：匹配块 {scene['matched_chunks']}，对白比例 {scene['stats']['dialogue_ratio']}")

    lines.extend(["", "## Constraints"])
    for level in ("hard", "inferred", "uncertain"):
        lines.append(f"### {level}")
        items = packet["constraints"][level]
        if not items:
            lines.append("- 无。")
        for item in items:
            evidence = ", ".join(item.get("evidence", []))
            suffix = f" 证据：{evidence}" if evidence else ""
            lines.append(f"- [{item['type']}] {item['content']}{suffix}")
    lines.append("### copyright_boundary")
    lines.extend(f"- {item}" for item in packet["constraints"]["copyright_boundary"])

    lines.extend(["", "## Draft Instructions"])
    lines.extend(f"- {item}" for item in packet["draft_instructions"])
    lines.extend(["", "## Self Checklist"])
    lines.extend(f"- {item}" for item in packet["self_checklist"])
    return "\n".join(lines).rstrip() + "\n"


def continuation_label(goal: dict[str, Any]) -> str:
    if goal["after_chapter"]:
        return f"after-chapter-{goal['after_chapter']}"
    if goal["after_chunk"]:
        return f"after-{goal['after_chunk']}"
    return "outline"


def write_continuation_pack(root: Path, book_id: str, packet: dict[str, Any]) -> list[str]:
    target = book_dir(root, book_id) / "continuations"
    target.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    label = continuation_label(packet["continuation_goal"])
    markdown = render_continuation_pack(packet)
    json_text = json.dumps(packet, ensure_ascii=False, indent=2) + "\n"
    paths = []
    for path, content in (
        (target / f"{stamp}-{label}.json", json_text),
        (target / f"{stamp}-{label}.md", markdown),
        (target / "continuation-pack-latest.json", json_text),
        (target / "continuation-pack-latest.md", markdown),
    ):
        path.write_text(content, encoding="utf-8")
        paths.append(str(path))
    return paths


def command_continue(args: argparse.Namespace) -> int:
    if args.scene and args.scene not in STYLE_SCENES:
        raise NovelReaderError("--scene 只支持：战斗、悬疑、感情、日常、说明。")
    if args.context_chunks < 1:
        raise NovelReaderError("--context-chunks 必须大于 0。")
    if args.evidence_top < 1:
        raise NovelReaderError("--evidence-top 必须大于 0。")
    root = storage_root(args)
    require_full_scope(root, args.book, "continue", args, args.after_chapter)
    packet = build_continuation_packet(root, args)
    if args.write:
        packet["output_paths"] = write_continuation_pack(root, args.book, packet)
        if args.json:
            print_json(packet)
        else:
            print_json({"ok": True, "paths": packet["output_paths"]})
        return 0
    if args.json:
        print_json(packet)
    else:
        print(render_continuation_pack(packet))
    return 0


def command_embed(args: argparse.Namespace) -> int:
    root = storage_root(args)
    manifest = load_manifest(root, args.book)
    provider = args.provider
    api_key, base_url, model = resolve_embedding_config(args.model)
    if not api_key:
        print_json(
            {
                "ok": False,
                "configured": False,
                "message": "未发现可用 embedding 服务。请先启动本地 Qwen，或配置 NOVEL_READER_EMBED_*。核心本地检索仍可使用。",
            }
        )
        return 2
    os.environ["NOVEL_READER_EMBED_API_KEY"] = api_key
    os.environ["NOVEL_READER_EMBED_BASE_URL"] = base_url
    os.environ["NOVEL_READER_EMBED_MODEL"] = model

    con = open_db(root, args.book)
    try:
        rows = list(con.execute("SELECT chunk_id, text FROM chunks ORDER BY chapter_index, chunk_index"))
        if args.limit:
            rows = rows[: args.limit]
        done = 0
        for i in range(0, len(rows), args.batch_size):
            batch = rows[i : i + args.batch_size]
            vectors = embed_texts([row["text"][: args.max_chars] for row in batch], provider, model)
            con.executemany(
                """
                INSERT INTO embeddings (chunk_id, provider, model, vector_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                  provider = excluded.provider,
                  model = excluded.model,
                  vector_json = excluded.vector_json,
                  updated_at = excluded.updated_at
                """,
                [
                    (row["chunk_id"], provider, model, json.dumps(vector), now_iso())
                    for row, vector in zip(batch, vectors)
                ],
            )
            con.commit()
            done += len(batch)
            if not args.quiet:
                print(f"embedded {done}/{len(rows)}", file=sys.stderr)
    finally:
        con.close()

    manifest["embedding"] = {
        "enabled": True,
        "provider": provider,
        "model": model,
        "chunk_count": done,
        "updated_at": now_iso(),
    }
    save_manifest(root, manifest)
    print_json({"ok": True, "provider": provider, "model": model, "chunks": done})
    return 0


def command_output(func: Any, args: argparse.Namespace) -> tuple[int, Any]:
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = int(func(args))
    except NovelReaderJsonError as exc:
        return exc.return_code, exc.payload
    text = buffer.getvalue().strip()
    if not text:
        return code, {}
    try:
        return code, json.loads(text)
    except json.JSONDecodeError:
        return code, {"text": text}


def do_unknown_response(route: IntentResult, as_json: bool) -> int:
    advice = {
        "ok": False,
        "route": route.to_dict(),
        "message": "无法判断需求。请尝试描述为：查看状态、读取第 N 章、搜索某剧情、问某问题、梳理剧情、生成地图、写作分析、风格分析、续写、建立语义索引。",
        "examples": [
            'novel-reader do <book> "这本书现在读到哪了"',
            'novel-reader do <book> "找一下小舞献祭"',
            'novel-reader do <book> "接第12章后面续写，短一点，偏悬疑"',
        ],
    }
    if as_json:
        print_json(advice)
    else:
        print(advice["message"])
        for example in advice["examples"]:
            print(f"- {example}")
    return 0


def route_namespace(args: argparse.Namespace, route: IntentResult) -> tuple[Any | None, argparse.Namespace | None]:
    suggested = dict(route.suggested_args)
    top = args.top or 8
    scene = args.scene or suggested.get("scene")
    length = args.length or suggested.get("length") or "medium"
    after_chapter = args.after_chapter if args.after_chapter is not None else suggested.get("after_chapter")
    after_chunk = args.after_chunk or suggested.get("chunk")
    chapter = suggested.get("chapter")
    chunk = suggested.get("chunk")
    query = suggested.get("query") or suggested.get("question") or suggested.get("outline") or clean_do_request(args.request)

    common = {"store": args.store, "book": args.book}
    scope = args.scope or suggested.get("scope") or "partial"
    if route.intent == "status":
        return command_status, argparse.Namespace(**common, json=args.json)
    if route.intent == "read":
        if not chapter and not chunk:
            return None, None
        return command_read, argparse.Namespace(**common, chapter=chapter, chunk=chunk, limit_chars=None, json=args.json)
    if route.intent == "search":
        return command_search, argparse.Namespace(
            **common,
            query=query,
            top=top,
            context_chars=360,
            semantic=args.semantic,
            json=args.json,
        )
    if route.intent == "ask":
        return command_ask, argparse.Namespace(
            **common,
            question=suggested.get("question") or query,
            top=top,
            context_chars=500,
            semantic=args.semantic,
            json=args.json,
        )
    if route.intent == "outline":
        return command_outline, argparse.Namespace(**common, write=args.write, json=args.json, scope=scope, allow_unfinalized=False)
    if route.intent == "map":
        return command_map, argparse.Namespace(**common, scope=scope, allow_unfinalized=False)
    if route.intent == "analyze":
        return command_analyze, argparse.Namespace(**common, scope=scope, allow_unfinalized=False, json=args.json)
    if route.intent == "style":
        return command_style, argparse.Namespace(**common, scene=scene, write=args.write, json=args.json, scope=scope, allow_unfinalized=False)
    if route.intent == "continue":
        return command_continue, argparse.Namespace(
            **common,
            after_chapter=after_chapter,
            after_chunk=after_chunk,
            outline=suggested.get("outline"),
            outline_file=None,
            semantic=args.semantic,
            scene=scene,
            length=length,
            context_chunks=5,
            evidence_top=top,
            write=args.write,
            json=args.json,
            scope=scope,
            allow_unfinalized=False,
        )
    if route.intent == "embed":
        return command_embed, argparse.Namespace(
            **common,
            provider="openai-compatible",
            model=None,
            batch_size=16,
            max_chars=2500,
            limit=None,
            quiet=False,
        )
    return None, None


def clean_do_request(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def command_do(args: argparse.Namespace) -> int:
    route = classify_request(args.request)
    if route.intent == "unknown":
        return do_unknown_response(route, args.json)

    func, routed_args = route_namespace(args, route)
    if func is None or routed_args is None:
        fallback = IntentResult(
            intent="unknown",
            confidence=route.confidence,
            reason=f"{route.reason}; missing required routing argument",
            suggested_args=route.suggested_args,
        )
        return do_unknown_response(fallback, args.json)

    if args.json:
        code, payload = command_output(func, routed_args)
        if code != 0 and isinstance(payload, dict) and "error" in payload:
            print_json({"ok": False, "route": route.to_dict(), "error": payload["error"]})
        else:
            print_json({"ok": code == 0, "route": route.to_dict(), "payload": payload})
        return code
    return int(func(routed_args))


def build_prose_generation_prompt(packet: dict[str, Any]) -> str:
    goal = packet["continuation_goal"]
    if goal.get("after_chunk"):
        anchor = goal["after_chunk"]
    elif goal.get("after_chapter"):
        anchor = f"第 {goal['after_chapter']} 章之后"
    else:
        anchor = "当前续写点"
    return "\n".join(
        [
            "你将根据 novel-reader continuation package 写原创续写正文。",
            f"续写位置：{anchor}",
            f"目标长度：{goal.get('target_length')}（{goal.get('target_length_hint')}）",
            f"场景倾向：{goal.get('scene') or '未指定'}",
            f"用户大纲：{goal.get('outline') or '未指定'}",
            "",
            "写作要求：",
            "1. 先阅读 recent_context、plot_evidence、style_evidence、constraints。",
            "2. 只把有证据的位置当作事实约束；证据不足处保持悬念或模糊处理。",
            "3. 保持人物动机、时间线、地点、设定和伏笔状态一致。",
            "4. 只能迁移抽象风格特征，不得复制原文连续表达、独特比喻或可识别段落。",
            "5. 输出原创正文后，附 self_checklist 并逐项说明是否通过。",
        ]
    )


def command_write_next(args: argparse.Namespace) -> int:
    if args.scene and args.scene not in STYLE_SCENES:
        raise NovelReaderError("--scene 只支持：战斗、悬疑、感情、日常、说明。")
    root = storage_root(args)
    require_full_scope(root, args.book, "continue", args, args.after_chapter)
    continuation_args = argparse.Namespace(
        store=args.store,
        book=args.book,
        after_chapter=args.after_chapter,
        after_chunk=args.after_chunk,
        outline=args.outline,
        outline_file=args.outline_file,
        semantic=False,
        scene=args.scene,
        length=args.length,
        context_chunks=5,
        evidence_top=8,
    )
    packet = build_continuation_packet(root, continuation_args)
    packet["prose_generation_prompt"] = build_prose_generation_prompt(packet)
    if args.json:
        print_json({"ok": True, "package": packet, "prose_generation_prompt": packet["prose_generation_prompt"]})
    else:
        print(render_continuation_pack(packet))
        print("\n## Prose Generation Prompt")
        print(packet["prose_generation_prompt"])
    return 0


def command_list(args: argparse.Namespace) -> int:
    root = storage_root(args)
    books = []
    if root.exists():
        for path in sorted(root.iterdir()):
            manifest_file = path / "manifest.json"
            if manifest_file.exists():
                books.append(json.loads(manifest_file.read_text(encoding="utf-8")))
    if args.json:
        print_json(books)
    else:
        for book in books:
            print(f"{book['book_id']}\t{book['title']}\t{book['chapter_count']} chapters")
    return 0


def selection_path(root: Path) -> Path:
    return root / "selection.json"


def command_select(args: argparse.Namespace) -> int:
    root = storage_root(args)
    if args.book:
        manifest = load_manifest(root, args.book)
        path = selection_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "book_id": manifest["book_id"],
            "title": manifest["title"],
            "selected_at": now_iso(),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print_json({"ok": True, "selected": data, "path": str(path)})
        return 0

    path = selection_path(root)
    if not path.exists():
        print_json({"ok": True, "selected": None})
        return 0
    print(path.read_text(encoding="utf-8").strip())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="novel-reader",
        description="Read, index, outline, ask about, and analyze long TXT/Markdown novels.",
    )
    parser.add_argument("--store", help="索引目录，默认当前目录下 .novel-reader，也可用 NOVEL_READER_HOME。")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="导入 TXT/Markdown 小说并建立索引。")
    p.add_argument("file")
    p.add_argument("--title")
    p.add_argument("--book-id")
    p.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    p.add_argument("--overlap-chars", type=int, default=DEFAULT_OVERLAP_CHARS)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=command_ingest)

    p = sub.add_parser("list", help="列出已导入书籍。")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_list)

    p = sub.add_parser("select", help="选择或查看当前默认书籍。")
    p.add_argument("book", nargs="?", help="要设为当前默认书籍的 book_id；省略则查看当前选择。")
    p.set_defaults(func=command_select)

    p = sub.add_parser("status", help="查看阅读进度、摘要覆盖率和索引状态。")
    p.add_argument("book")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_status)

    p = sub.add_parser("read", help="按章节或块读取原文。")
    p.add_argument("book")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--chapter", type=int)
    group.add_argument("--chunk")
    p.add_argument("--limit-chars", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_read)

    p = sub.add_parser("search", help="检索原文片段。")
    p.add_argument("book")
    p.add_argument("query")
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--context-chars", type=int, default=360)
    p.add_argument("--semantic", action="store_true", help="使用已建立的 embedding 索引。")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_search)

    p = sub.add_parser("ask", help="生成带证据的情节问答包。")
    p.add_argument("book")
    p.add_argument("question")
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--context-chars", type=int, default=500)
    p.add_argument("--semantic", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_ask)

    p = sub.add_parser("note", help="记录模型生成的章节摘要，用于覆盖率与全书地图。")
    p.add_argument("book")
    p.add_argument("--chapter", type=int, required=True)
    source = p.add_mutually_exclusive_group()
    source.add_argument("--text")
    source.add_argument("--file")
    p.set_defaults(func=command_note)

    p = sub.add_parser("read-session", help="Create a governed full-book reading session.")
    p.add_argument("book")
    p.add_argument("--goal", choices=("full",), default="full")
    p.add_argument("--mode", choices=("survey", "balanced", "deep"), default="balanced")
    p.add_argument("--deep-ratio", type=float, default=0.25)
    p.add_argument("--query")
    p.add_argument("--focus-chapter", type=int)
    p.add_argument("--after-chapter", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_read_session)

    p = sub.add_parser("read-next", help="Return the next required chapter batch for a reading session.")
    p.add_argument("session_id")
    p.add_argument("--batch-chapters", type=int, default=1)
    p.add_argument("--chapter", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_read_next)

    p = sub.add_parser("submit-note", help="Submit and validate a governed chapter note.")
    p.add_argument("session_id")
    p.add_argument("--chapter", type=int, required=True)
    source = p.add_mutually_exclusive_group()
    source.add_argument("--text")
    source.add_argument("--file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_submit_note)

    p = sub.add_parser("reading-status", help="Show governed reading-session coverage.")
    p.add_argument("session_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_reading_status)

    p = sub.add_parser("finalize-reading", help="Finalize a reading session once required levels are complete.")
    p.add_argument("session_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_finalize_reading)

    p = sub.add_parser("outline", help="汇总章节摘要，生成情节梳理草案。")
    p.add_argument("book")
    p.add_argument("--write", action="store_true", help="写入 maps/outline.md，否则输出到终端。")
    p.add_argument("--scope", choices=("partial", "full"), default="partial")
    p.add_argument("--allow-unfinalized", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_outline)

    p = sub.add_parser("map", help="生成全书地图草案。")
    p.add_argument("book")
    p.add_argument("--scope", choices=("partial", "full"), default="partial")
    p.add_argument("--allow-unfinalized", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_map)

    p = sub.add_parser("analyze", help="生成写作分析报告草案。")
    p.add_argument("book")
    p.add_argument("--scope", choices=("partial", "full"), default="partial")
    p.add_argument("--allow-unfinalized", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_analyze)

    p = sub.add_parser("style", help="Distill language style evidence and original-writing guidance.")
    p.add_argument("book")
    p.add_argument("--scene", choices=STYLE_SCENES, help="Focus on one scene type: 战斗、悬疑、感情、日常、说明.")
    p.add_argument("--write", action="store_true", help="Write style artifacts under styles/.")
    p.add_argument("--json", action="store_true", help="Return a structured evidence packet.")
    p.add_argument("--scope", choices=("partial", "full"), default="partial")
    p.add_argument("--allow-unfinalized", action="store_true")
    p.set_defaults(func=command_style)

    p = sub.add_parser("continue", help="Build a continuation writing package without generating prose.")
    p.add_argument("book")
    anchor = p.add_mutually_exclusive_group()
    anchor.add_argument("--after-chapter", type=int, help="Continue after the end of this chapter.")
    anchor.add_argument("--after-chunk", help="Continue after this chunk id, such as c0001-001.")
    outline = p.add_mutually_exclusive_group()
    outline.add_argument("--outline", help="User-provided continuation outline.")
    outline.add_argument("--outline-file", help="Read continuation outline from a UTF-8 text/Markdown file.")
    p.add_argument("--semantic", action="store_true", help="Use embedding search when the book has an embedding index.")
    p.add_argument("--scene", choices=STYLE_SCENES, help="Focus style evidence on one scene type.")
    p.add_argument("--length", choices=tuple(CONTINUATION_LENGTHS), default="medium")
    p.add_argument("--context-chunks", type=int, default=5)
    p.add_argument("--evidence-top", type=int, default=8)
    p.add_argument("--write", action="store_true", help="Write JSON/Markdown continuation packs under continuations/.")
    p.add_argument("--json", action="store_true", help="Return a structured continuation package.")
    p.add_argument("--scope", choices=("partial", "full"), default="partial")
    p.add_argument("--allow-unfinalized", action="store_true")
    p.set_defaults(func=command_continue)

    p = sub.add_parser("do", help="Natural-language unified entrypoint for common novel-reader tasks.")
    p.add_argument("book")
    p.add_argument("request")
    p.add_argument("--json", action="store_true")
    p.add_argument("--semantic", action="store_true")
    p.add_argument("--write", action="store_true")
    p.add_argument("--after-chapter", type=int)
    p.add_argument("--after-chunk")
    p.add_argument("--scene", choices=STYLE_SCENES)
    p.add_argument("--length", choices=tuple(CONTINUATION_LENGTHS), default=None)
    p.add_argument("--top", type=int)
    p.add_argument("--scope", choices=("partial", "full"), default=None)
    p.set_defaults(func=command_do)

    p = sub.add_parser("write-next", help="Build a continuation package plus an agent prose-generation prompt.")
    p.add_argument("book")
    anchor = p.add_mutually_exclusive_group()
    anchor.add_argument("--after-chapter", type=int, help="Continue after the end of this chapter.")
    anchor.add_argument("--after-chunk", help="Continue after this chunk id, such as c0001-001.")
    outline = p.add_mutually_exclusive_group()
    outline.add_argument("--outline", help="User-provided continuation outline.")
    outline.add_argument("--outline-file", help="Read continuation outline from a UTF-8 text/Markdown file.")
    p.add_argument("--scene", choices=STYLE_SCENES)
    p.add_argument("--length", choices=tuple(CONTINUATION_LENGTHS), default="medium")
    p.add_argument("--json", action="store_true")
    p.add_argument("--scope", choices=("partial", "full"), default="partial")
    p.add_argument("--allow-unfinalized", action="store_true")
    p.set_defaults(func=command_write_next)

    p = sub.add_parser("embed", help="可选：用 openai-compatible embedding 增强语义检索。")
    p.add_argument("book")
    p.add_argument("--provider", default="openai-compatible")
    p.add_argument("--model")
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--max-chars", type=int, default=2500)
    p.add_argument("--limit", type=int, help="调试用：只处理前 N 个块。")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=command_embed)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except NovelReaderJsonError as exc:
        print_json(exc.payload)
        return exc.return_code
    except (NovelReaderError, ValueError) as exc:
        print(f"novel-reader: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
