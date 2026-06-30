from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .storage import load_manifest, open_db


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
    # Lazy import to avoid a circular import: cli.py imports names from this
    # module at the top level, so this module must not import from cli at the
    # module level. NovelReaderError is only needed here when raising.
    from .cli import NovelReaderError

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
