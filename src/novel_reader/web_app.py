from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import shutil
import subprocess
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, request, send_file, send_from_directory

from . import cli
from . import reading_session


STATIC_DIR = Path(__file__).resolve().parent / "web_static"
DOCUMENT_DIRS = ("maps", "reports", "styles", "continuations", "summaries")
DOCUMENT_EXTS = {".md", ".json", ".txt"}
CLAUDE_TIMEOUT_SECONDS = 240
MAX_CLAUDE_CONTEXT_CHARS = 80000


def make_app() -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.config["NOVEL_READER_CSRF_TOKEN"] = os.environ.get("NOVEL_READER_WEB_TOKEN") or secrets.token_urlsafe(32)

    @app.before_request
    def check_csrf() -> Any:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            token = request.headers.get("X-Novel-Reader-Token", "")
            if token != app.config["NOVEL_READER_CSRF_TOKEN"]:
                return jsonify({"ok": False, "error": "CSRF token missing or invalid."}), 403
        return None

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/health")
    def api_health():
        return ok(
            {
                "app": "novel-reader-web",
                "csrf_token": app.config["NOVEL_READER_CSRF_TOKEN"],
                "embedding": embedding_status(),
                "claude": claude_status(),
                "store": str(cli.storage_root(namespace())),
            }
        )

    @app.get("/api/books")
    def api_books():
        root = cli.storage_root(namespace())
        books = []
        if root.exists():
            for path in sorted(root.iterdir()):
                manifest = path / "manifest.json"
                if not manifest.exists():
                    continue
                data = json.loads(manifest.read_text(encoding="utf-8"))
                summaries = cli.fetch_summary_rows(root, data["book_id"])
                data["summary_coverage"] = {
                    "summarized_chapters": len(summaries),
                    "percent": round(len(summaries) * 100 / max(int(data["chapter_count"]), 1), 2),
                }
                books.append(data)
        return ok({"books": books})

    @app.get("/api/status/<book_id>")
    def api_status(book_id: str):
        return ok(run_command_json(cli.command_status, book=book_id, json=True))

    @app.post("/api/ingest")
    def api_ingest():
        payload = request_json()
        path = str(payload.get("path", "")).strip()
        if not path:
            raise cli.NovelReaderError("请输入 TXT/Markdown 文件路径。")
        source = Path(path).expanduser()
        if not source.exists():
            raise cli.NovelReaderError(f"文件不存在：{source}")
        if source.suffix.lower() not in {".txt", ".md", ".markdown"}:
            raise cli.NovelReaderError("只支持 .txt、.md、.markdown。")
        return ok(
            run_command_json(
                cli.command_ingest,
                file=str(source),
                title=payload.get("title") or None,
                book_id=payload.get("book_id") or None,
                chunk_chars=int(payload.get("chunk_chars") or cli.DEFAULT_CHUNK_CHARS),
                overlap_chars=int(payload.get("overlap_chars") or cli.DEFAULT_OVERLAP_CHARS),
                force=bool(payload.get("force")),
            )
        )

    @app.get("/api/read")
    def api_read():
        book = required_arg("book")
        chapter = request.args.get("chapter", type=int)
        chunk = request.args.get("chunk")
        limit_chars = request.args.get("limit_chars", type=int)
        rows = cli.get_chunks(cli.storage_root(namespace()), book, chapter=chapter, chunk_id=chunk, limit_chars=limit_chars)
        return ok({"chunks": [dict(row) for row in rows]})

    @app.post("/api/search")
    def api_search():
        payload = request_json()
        book = required_payload(payload, "book")
        query = required_payload(payload, "query")
        results = cli.search_book(
            cli.storage_root(namespace()),
            book,
            query,
            int(payload.get("top") or 8),
            int(payload.get("context_chars") or 360),
            bool(payload.get("semantic")),
        )
        return ok({"results": results})

    @app.post("/api/ask")
    def api_ask():
        payload = request_json()
        packet = run_command_json(
            cli.command_ask,
            book=required_payload(payload, "book"),
            question=required_payload(payload, "question"),
            top=int(payload.get("top") or 8),
            context_chars=int(payload.get("context_chars") or 500),
            semantic=bool(payload.get("semantic")),
            json=True,
        )
        return ok(packet)

    @app.post("/api/action/<action>")
    def api_action(action: str):
        payload = request_json()
        book = required_payload(payload, "book")
        scope = validate_scope(payload.get("scope") or "partial")
        allow_unfinalized = bool(payload.get("allow_unfinalized"))
        session_id = payload.get("session_id") or None
        if action == "outline":
            data = run_command_json(
                cli.command_outline,
                book=book,
                write=True,
                json=True,
                scope=scope,
                allow_unfinalized=allow_unfinalized,
                session_id=session_id,
            )
        elif action == "map":
            data = run_command_json(
                cli.command_map,
                book=book,
                json=True,
                scope=scope,
                allow_unfinalized=allow_unfinalized,
                session_id=session_id,
            )
        elif action == "analyze":
            data = run_command_json(
                cli.command_analyze,
                book=book,
                json=True,
                scope=scope,
                allow_unfinalized=allow_unfinalized,
                session_id=session_id,
            )
        else:
            raise cli.NovelReaderError(f"未知操作：{action}")
        return ok(with_document_refs(book, data))

    @app.post("/api/style")
    def api_style():
        payload = request_json()
        book = required_payload(payload, "book")
        scene = validate_scene(payload.get("scene") or None)
        packet = run_command_json(
            cli.command_style,
            book=book,
            scene=scene,
            write=bool(payload.get("write")),
            json=bool(payload.get("json", True)),
            scope=validate_scope(payload.get("scope") or "partial"),
            allow_unfinalized=bool(payload.get("allow_unfinalized")),
            session_id=payload.get("session_id") or None,
        )
        return ok(with_document_refs(book, packet))

    @app.post("/api/continue")
    def api_continue():
        payload = request_json()
        args = namespace(
            book=required_payload(payload, "book"),
            after_chapter=maybe_int(payload.get("after_chapter")),
            after_chunk=payload.get("after_chunk") or None,
            outline=payload.get("outline") or None,
            outline_file=payload.get("outline_file") or None,
            semantic=bool(payload.get("semantic")),
            scene=validate_scene(payload.get("scene") or None),
            length=payload.get("length") or "medium",
            context_chunks=int(payload.get("context_chunks") or 5),
            evidence_top=int(payload.get("evidence_top") or 8),
            write=bool(payload.get("write")),
            json=True,
            scope=validate_scope(payload.get("scope") or "partial"),
            allow_unfinalized=bool(payload.get("allow_unfinalized")),
            session_id=payload.get("session_id") or None,
        )
        packet = run_command_json(cli.command_continue, **vars(args))
        return ok(with_document_refs(args.book, packet))

    @app.post("/api/predict")
    def api_predict():
        payload = request_json()
        packet = run_command_json(
            cli.command_predict,
            book=required_payload(payload, "book"),
            question=payload.get("question") or None,
            scope=payload.get("scope") or "general",
            horizon=payload.get("horizon") or "next-arc",
            anchor_chapter=maybe_int(payload.get("anchor_chapter")),
            anchor_chunk=payload.get("anchor_chunk") or None,
            top=int(payload.get("top") or 8),
            context_chunks=int(payload.get("context_chunks") or 5),
            semantic=bool(payload.get("semantic")),
            write=bool(payload.get("write")),
            json=True,
            scope_mode=validate_scope(payload.get("scope_mode") or "partial"),
            session_id=payload.get("session_id") or None,
            allow_unfinalized=bool(payload.get("allow_unfinalized")),
        )
        return ok(with_document_refs(packet["book"]["id"], packet))

    @app.post("/api/embed")
    def api_embed():
        payload = request_json()
        data = run_command_json(
            cli.command_embed,
            book=required_payload(payload, "book"),
            provider="openai-compatible",
            model=payload.get("model") or None,
            batch_size=int(payload.get("batch_size") or 4),
            max_chars=int(payload.get("max_chars") or 1500),
            limit=maybe_int(payload.get("limit")),
            quiet=True,
        )
        return ok(data)

    @app.post("/api/reading/session")
    def api_reading_session():
        payload = request_json()
        result = reading_session.create_session(
            cli.storage_root(namespace()),
            required_payload(payload, "book"),
            str(payload.get("goal") or "full"),
            str(payload.get("mode") or "balanced"),
            float(payload.get("deep_ratio") or 0.25),
            query=payload.get("query") or None,
            focus_chapter=maybe_int(payload.get("focus_chapter")),
            after_chapter=maybe_int(payload.get("after_chapter")),
        )
        return ok(result)

    @app.get("/api/reading/session/<session_id>/status")
    def api_reading_session_status(session_id: str):
        return ok(reading_session.calculate_status(cli.storage_root(namespace()), session_id))

    @app.post("/api/reading/session/<session_id>/next")
    def api_reading_session_next(session_id: str):
        payload = request_json()
        result = reading_session.build_read_next(
            cli.storage_root(namespace()),
            session_id,
            int(payload.get("batch_chapters") or 1),
            maybe_int(payload.get("chapter")),
        )
        return ok(result)

    @app.post("/api/reading/session/<session_id>/submit-note")
    def api_reading_session_submit_note(session_id: str):
        payload = request_json()
        result = reading_session.submit_note(
            cli.storage_root(namespace()),
            session_id,
            int(payload.get("chapter") or 0),
            str(payload.get("text") or ""),
        )
        status_code = 200 if result.get("ok") else 400
        return jsonify(result), status_code

    @app.post("/api/reading/session/<session_id>/finalize")
    def api_reading_session_finalize(session_id: str):
        result = reading_session.finalize_session(cli.storage_root(namespace()), session_id)
        status_code = 200 if result.get("ok", result.get("final_reports_allowed")) else 400
        return jsonify(result), status_code

    @app.get("/api/documents")
    def api_documents():
        book = required_arg("book")
        return ok({"documents": list_documents(book)})

    @app.get("/api/document")
    def api_document():
        book = required_arg("book")
        rel_path = required_arg("path")
        path = safe_document_path(book, rel_path)
        content = path.read_text(encoding="utf-8", errors="replace")
        return ok(
            {
                "document": document_info(book, path),
                "content": content,
            }
        )

    @app.get("/api/document/download")
    def api_document_download():
        book = required_arg("book")
        rel_path = required_arg("path")
        path = safe_document_path(book, rel_path)
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.get("/api/claude/status")
    def api_claude_status():
        return ok(claude_status())

    @app.post("/api/claude/chat")
    def api_claude_chat():
        payload = request_json()
        status = claude_status()
        if not status["enabled"]:
            return jsonify({"ok": False, "error": "Claude bridge is not enabled. Restart with -EnableClaudeChat."}), 403
        if not status["available"]:
            return jsonify({"ok": False, "error": "claude was not found on PATH."}), 400
        mode = str(payload.get("mode") or "once")
        if mode not in {"once", "continue"}:
            raise cli.NovelReaderError("mode 只支持 once 或 continue。")
        if not claude_mode_allowed(mode, status["mode"]):
            raise cli.NovelReaderError(f"当前启动模式不允许 Claude {mode} 调用。")
        message = required_payload(payload, "message")
        prompt = build_claude_prompt(
            message=message,
            book=payload.get("book"),
            context=payload.get("context") if isinstance(payload.get("context"), dict) else {},
        )
        result = call_claude(prompt, mode, status["permission"])
        return ok(result)

    @app.errorhandler(cli.NovelReaderError)
    def handle_novel_error(exc: cli.NovelReaderError):
        return jsonify({"ok": False, "error": str(exc)}), 400

    @app.errorhandler(cli.NovelReaderJsonError)
    def handle_novel_json_error(exc: cli.NovelReaderJsonError):
        return jsonify({"ok": False, "error": exc.payload.get("error", exc.payload)}), 400

    @app.errorhandler(ValueError)
    def handle_value_error(exc: ValueError):
        return jsonify({"ok": False, "error": str(exc)}), 400

    @app.errorhandler(Exception)
    def handle_error(exc: Exception):
        return jsonify({"ok": False, "error": str(exc)}), 500

    return app


def namespace(**kwargs: Any) -> argparse.Namespace:
    values = {"store": None}
    values.update(kwargs)
    return argparse.Namespace(**values)


def request_json() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return {}
    return data


def required_arg(name: str) -> str:
    value = request.args.get(name, "").strip()
    if not value:
        raise cli.NovelReaderError(f"缺少参数：{name}")
    return value


def required_payload(payload: dict[str, Any], name: str) -> str:
    value = str(payload.get(name, "")).strip()
    if not value:
        raise cli.NovelReaderError(f"缺少参数：{name}")
    return value


def maybe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def validate_scene(scene: Any) -> str | None:
    if not scene:
        return None
    scene = str(scene)
    if scene not in cli.STYLE_SCENES:
        raise cli.NovelReaderError("scene 只支持：战斗、悬疑、感情、日常、说明。")
    return scene


def validate_scope(scope: Any) -> str:
    value = str(scope or "partial")
    if value not in {"partial", "full"}:
        raise cli.NovelReaderError("scope must be partial or full.")
    return value


def run_command_json(func: Callable[[argparse.Namespace], int], **kwargs: Any) -> dict[str, Any]:
    buffer = io.StringIO()
    args = namespace(**kwargs)
    try:
        with redirect_stdout(buffer):
            result = func(args)
    except cli.NovelReaderJsonError:
        raise
    text = buffer.getvalue().strip()
    if result not in (0, None):
        raise cli.NovelReaderError(text or f"命令失败：{result}")
    if not text:
        return {"ok": True}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": True, "text": text}


def ok(data: dict[str, Any]) -> Any:
    if "ok" not in data:
        data = {"ok": True, **data}
    return jsonify(data)


def embedding_status() -> dict[str, Any]:
    base_url = os.environ.get("NOVEL_READER_EMBED_BASE_URL", "").rstrip("/")
    if not base_url:
        return {"configured": False, "available": False, "base_url": None, "local": False, "vector_backend": "sqlite_cosine"}
    local = base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost")
    try:
        health_url = base_url.removesuffix("/v1") + "/health"
        with urllib.request.urlopen(health_url, timeout=2) as response:
            health = json.loads(response.read().decode("utf-8"))
        return {"configured": True, "available": bool(health.get("ok")), "base_url": base_url, "local": local, "health": health, "vector_backend": "sqlite_cosine"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {"configured": True, "available": False, "base_url": base_url, "local": local, "vector_backend": "sqlite_cosine"}


def book_root(book_id: str) -> Path:
    root = cli.storage_root(namespace())
    cli.load_manifest(root, book_id)
    return cli.book_dir(root, book_id).resolve()


def path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def safe_document_path(book_id: str, rel_path: str) -> Path:
    root = book_root(book_id)
    raw = Path(rel_path)
    if raw.is_absolute() or any(part in {"..", ""} for part in raw.parts):
        raise cli.NovelReaderError("文档路径不合法。")
    if not raw.parts or raw.parts[0] not in DOCUMENT_DIRS:
        raise cli.NovelReaderError("该文档目录不允许在网页中读取。")
    if raw.suffix.lower() not in DOCUMENT_EXTS:
        raise cli.NovelReaderError("该文档类型不允许在网页中读取。")
    path = (root / raw).resolve()
    if not path_within(path, root) or not path.is_file():
        raise cli.NovelReaderError("文档不存在或越界。")
    return path


def document_info(book_id: str, path: Path) -> dict[str, Any]:
    root = book_root(book_id)
    stat = path.stat()
    rel = path.relative_to(root).as_posix()
    return {
        "book_id": book_id,
        "path": rel,
        "name": path.name,
        "category": Path(rel).parts[0],
        "extension": path.suffix.lower(),
        "size": stat.st_size,
        "updated_at": stat.st_mtime,
    }


def list_documents(book_id: str) -> list[dict[str, Any]]:
    root = book_root(book_id)
    docs: list[dict[str, Any]] = []
    for folder in DOCUMENT_DIRS:
        base = root / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() in DOCUMENT_EXTS:
                docs.append(document_info(book_id, path.resolve()))
    docs.sort(key=lambda item: (item["category"], item["path"]))
    return docs


def relative_document_ref(book_id: str, maybe_path: Any) -> dict[str, Any] | None:
    if not maybe_path:
        return None
    try:
        root = book_root(book_id)
        path = Path(str(maybe_path)).resolve()
        if not path_within(path, root):
            return None
        rel = path.relative_to(root).as_posix()
        return document_info(book_id, safe_document_path(book_id, rel))
    except (OSError, ValueError, cli.NovelReaderError):
        return None


def with_document_refs(book_id: str, data: dict[str, Any]) -> dict[str, Any]:
    refs = []
    for key in ("path", "paths", "output_paths"):
        value = data.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            ref = relative_document_ref(book_id, item)
            if ref and ref not in refs:
                refs.append(ref)
    if refs:
        data["documents"] = refs
    return data


def claude_status() -> dict[str, Any]:
    enabled = os.environ.get("NOVEL_READER_CLAUDE_ENABLED") == "1"
    mode = os.environ.get("NOVEL_READER_CLAUDE_MODE", "both")
    if mode not in {"once", "continue", "both"}:
        mode = "both"
    permission = os.environ.get("NOVEL_READER_CLAUDE_PERMISSION", "normal")
    if permission not in {"normal", "dangerous"}:
        permission = "normal"
    executable = shutil.which("claude")
    return {
        "enabled": enabled,
        "available": bool(executable),
        "executable": executable,
        "mode": mode,
        "permission": permission,
    }


def claude_mode_allowed(requested: str, configured: str) -> bool:
    return configured == "both" or requested == configured


def build_claude_prompt(message: str, book: Any, context: dict[str, Any]) -> str:
    context_text = json.dumps(context, ensure_ascii=False, indent=2)
    if len(context_text) > MAX_CLAUDE_CONTEXT_CHARS:
        context_text = context_text[:MAX_CLAUDE_CONTEXT_CHARS] + "\n...[context truncated]"
    book_line = f"当前 book_id：{book}" if book else "当前 book_id：未指定"
    return "\n".join(
        [
            "你正在通过 Novel Reader Web 控制台协助用户阅读和分析长篇小说。",
            book_line,
            "请优先依据下方 novel-reader 证据包、文档或任务包回答；证据不足时明确说明不足，不要编造。",
            "如果用户要求续写，请保持原创表达，不复制原文连续表达，不生成直接仿冒某作者的提示词。",
            "",
            "【用户消息】",
            message,
            "",
            "【可用上下文】",
            context_text,
        ]
    )


def call_claude(prompt: str, mode: str, permission: str) -> dict[str, Any]:
    executable = shutil.which("claude")
    if not executable:
        raise cli.NovelReaderError("claude was not found on PATH.")
    args = [executable]
    if mode == "continue":
        args.append("-c")
    args.extend(["-p", "--output-format", "json"])
    if permission == "dangerous":
        args.append("--dangerously-skip-permissions")
    args.append(prompt)
    completed = subprocess.run(
        args,
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=CLAUDE_TIMEOUT_SECONDS,
        check=False,
    )
    parsed = None
    reply = completed.stdout.strip()
    try:
        parsed = json.loads(completed.stdout)
        reply = extract_claude_reply(parsed) or reply
    except json.JSONDecodeError:
        parsed = None
    if completed.returncode != 0:
        raise cli.NovelReaderError(completed.stderr.strip() or completed.stdout.strip() or f"Claude exited with {completed.returncode}.")
    return {
        "mode": mode,
        "permission": permission,
        "reply": reply,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "parsed": parsed,
    }


def extract_claude_reply(parsed: Any) -> str | None:
    if isinstance(parsed, dict):
        for key in ("result", "response", "text", "message"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        content = parsed.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            if parts:
                return "\n".join(parts).strip()
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Novel Reader local web console.")
    parser.add_argument("--host", default=os.environ.get("NOVEL_READER_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("NOVEL_READER_WEB_PORT", "8765")))
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        print("Warning: binding outside 127.0.0.1 may expose novel content on your network.")
    make_app().run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
