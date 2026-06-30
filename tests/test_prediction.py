import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from novel_reader.intent_router import classify_request
from novel_reader import predictor
from novel_reader.web_app import make_app

from test_reading_session import import_book, load_json, run_cli, valid_l1_note, valid_l2_note


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "novel-reader"


def csrf_headers(app) -> dict[str, str]:
    return {"X-Novel-Reader-Token": app.config["NOVEL_READER_CSRF_TOKEN"]}


def complete_balanced_session(store: Path, book: str) -> dict:
    session = load_json(run_cli(store, "read-session", book, "--mode", "balanced", "--deep-ratio", "0.5", "--json"))
    for chapter in range(1, 5):
        packet = load_json(run_cli(store, "read-next", session["session_id"], "--chapter", str(chapter), "--json"))
        chunks = [item["chunk_id"] for item in packet["chapters"][0]["chunks"]]
        required = packet["chapters"][0]["required_level"]
        note = valid_l2_note(chunks) if required == "L2_READ" else valid_l1_note(chunks[0])
        run_cli(store, "submit-note", session["session_id"], "--chapter", str(chapter), "--text", note, "--json")
    return session


def run_raw_cli(store: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(CLI), "--store", str(store), *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        env=env,
        capture_output=True,
        check=True,
    )


def import_prediction_fixture(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "prediction-fixture.md"
    source.write_text(
        "\n".join(
            [
                "# Chapter 1",
                "ancient prophecy secret heirloom hidden truth bloodline oath " * 40,
                "# Chapter 2",
                "betrayal clue hidden map ancient promise mystery " * 40,
                "# Chapter 3",
                "daily travel market cooking harmless talk " * 40,
                "# Chapter 4",
                "training school ordinary friendship study " * 40,
                "# Chapter 5",
                "recent siege enemy conflict attack escape urgent danger " * 40,
                "# Chapter 6",
                "current battle breakthrough enemy pursuit decision cost " * 40,
            ]
        ),
        encoding="utf-8",
    )
    store = tmp_path / "store"
    run_raw_cli(store, "ingest", str(source), "--book-id", "prediction-fixture", "--title", "Prediction Fixture", "--chunk-chars", "900")
    return store, "prediction-fixture"


def test_predict_intent_keywords_do_not_override_continue():
    assert classify_request("这本未完结小说后面可能怎么发展？").intent == "predict"
    assert classify_request("后续剧情可能是什么？").intent == "predict"
    assert classify_request("主角会不会背叛宗门？").intent == "predict"
    assert classify_request("接第12章后面续写").intent == "continue"
    assert classify_request("续写第12章后面的剧情").intent == "continue"
    assert classify_request("写下一章").intent == "continue"


def test_predict_cli_json_returns_evidence_based_predictions(tmp_path: Path):
    store, book = import_book(tmp_path)

    data = load_json(run_cli(store, "predict", book, "后续剧情可能怎么发展？", "--json"))

    assert data["ok"] is True
    assert data["prediction_goal"]["scope"] == "general"
    assert data["predictions"]
    first = data["predictions"][0]
    assert first["probability"] in {"high", "medium", "low"}
    assert 0 <= first["confidence"] <= 1
    assert first["supporting_evidence"]
    assert "risk" in first
    assert data["evidence"]


def test_predict_anchor_chapter_limits_prediction_sources(tmp_path: Path):
    store, book = import_book(tmp_path)

    data = load_json(run_cli(store, "predict", book, "只根据前三章预测后续", "--anchor-chapter", "3", "--json"))

    assert data["current_state"]["latest_chapter"] == 3
    assert all(item["chapter"] <= 3 for item in data["current_state"]["recent_context"])
    assert all(item["chapter"] <= 3 for item in data["current_state"]["open_threads"])
    assert all(item["chapter"] <= 3 for item in data["evidence"])


def test_predict_combines_global_early_threads_with_recent_state(tmp_path: Path):
    store, book = import_prediction_fixture(tmp_path)

    data = load_json(run_cli(store, "predict", book, "predict hidden truth and current battle", "--anchor-chapter", "6", "--top", "8", "--json"))

    evidence_chapters = {item["chapter"] for item in data["evidence"]}
    global_thread_chapters = {item["chapter"] for item in data["current_state"]["global_threads"]}
    recent_chapters = {item["chapter"] for item in data["current_state"]["recent_context"]}
    reasoning = "\n".join(reason for prediction in data["predictions"] for reason in prediction["reasoning"])

    assert evidence_chapters & {1, 2}
    assert evidence_chapters & {5, 6}
    assert global_thread_chapters & {1, 2}
    assert recent_chapters & {5, 6}
    assert "全前文" in reasoning
    assert "近期剧情状态" in reasoning


def test_do_routes_prediction_requests_to_predict(tmp_path: Path):
    store, book = import_book(tmp_path)

    data = load_json(run_cli(store, "do", book, "后续剧情可能怎么发展？", "--json"))

    assert data["ok"] is True
    assert data["route"]["intent"] == "predict"
    assert data["payload"]["predictions"]


def test_predict_write_creates_latest_markdown_and_json(tmp_path: Path):
    store, book = import_book(tmp_path)

    data = load_json(run_cli(store, "predict", book, "伏笔会怎么回收？", "--write", "--json"))

    assert data["ok"] is True
    paths = [Path(path) for path in data["output_paths"]]
    assert store / book / "predictions" / "prediction-latest.md" in paths
    assert store / book / "predictions" / "prediction-latest.json" in paths
    for path in paths:
        assert path.exists()
    latest_json = json.loads((store / book / "predictions" / "prediction-latest.json").read_text(encoding="utf-8"))
    latest_md = (store / book / "predictions" / "prediction-latest.md").read_text(encoding="utf-8")
    assert latest_json["prompt_path"]
    assert "prediction-prompt" in latest_md


def test_predict_llm_falls_back_when_claude_unavailable(tmp_path: Path, monkeypatch):
    store, book = import_book(tmp_path)
    monkeypatch.setattr(predictor.shutil, "which", lambda name: None)
    args = SimpleNamespace(
        question="后续剧情可能怎么发展？",
        scope="general",
        horizon="next-arc",
        anchor_chapter=None,
        anchor_chunk=None,
        context_chunks=5,
        top=8,
        semantic=False,
    )

    packet = predictor.build_prediction_packet(store, book, args, use_llm=True)

    assert packet["ok"] is True
    assert packet["prediction_goal"]["use_llm"] is True
    assert packet["predictions"][0]["source"] == "template"
    assert any("claude CLI" in item for item in packet["warnings"])


def test_predict_llm_packet_keeps_prompt_md_for_audit(tmp_path: Path, monkeypatch):
    """use_llm=True 时 packet 必须保留 prompt_md，便于审计实际发给 Claude 的 prompt。"""
    store, book = import_book(tmp_path)
    monkeypatch.setattr(predictor.shutil, "which", lambda name: None)
    args = SimpleNamespace(
        question="后续剧情可能怎么发展？",
        scope="general",
        horizon="next-arc",
        anchor_chapter=None,
        anchor_chunk=None,
        context_chunks=5,
        top=8,
        semantic=False,
    )

    packet = predictor.build_prediction_packet(store, book, args, use_llm=True)

    assert packet["prompt_md"]
    assert "小说预测分析请求" in packet["prompt_md"]
    assert any("claude CLI" in item for item in packet["warnings"])


def test_predict_llm_write_creates_prompt_files_even_on_fallback(tmp_path: Path, monkeypatch):
    """claude 不可用 fallback 时，--write 也必须写入 prompt 文件。"""
    store, book = import_book(tmp_path)
    monkeypatch.setattr(predictor.shutil, "which", lambda name: None)
    args = SimpleNamespace(
        question="后续剧情可能怎么发展？",
        scope="general",
        horizon="next-arc",
        anchor_chapter=None,
        anchor_chunk=None,
        context_chunks=5,
        top=8,
        semantic=False,
    )
    packet = predictor.build_prediction_packet(store, book, args, use_llm=True)
    paths = predictor.write_prediction_packet(store, book, packet)

    path_objs = [Path(p) for p in paths]
    assert store / book / "predictions" / "prediction-prompt-latest.md" in path_objs
    prompt_files = [p for p in path_objs if p.name.endswith("-prediction-prompt.md")]
    assert prompt_files, "timestamped prompt file must be written"
    for path in path_objs:
        assert path.exists()
    prompt_content = (store / book / "predictions" / "prediction-prompt-latest.md").read_text(encoding="utf-8")
    assert "小说预测分析请求" in prompt_content


def test_predict_llm_write_creates_prompt_files_on_success(tmp_path: Path, monkeypatch):
    """claude 可用且返回有效 JSON 时，--write 也必须写入 prompt 文件。"""
    store, book = import_book(tmp_path)
    llm_payload = {
        "result": json.dumps(
            {"predictions": [], "alternative_scenarios": [], "watchlist": []},
            ensure_ascii=False,
        )
    }

    def fake_run(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(llm_payload, ensure_ascii=False), stderr="")

    monkeypatch.setattr(predictor.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(predictor.subprocess, "run", fake_run)
    args = SimpleNamespace(
        question="后续剧情可能怎么发展？",
        scope="general",
        horizon="next-arc",
        anchor_chapter=None,
        anchor_chunk=None,
        context_chunks=5,
        top=8,
        semantic=False,
    )

    packet = predictor.build_prediction_packet(store, book, args, use_llm=True)
    paths = predictor.write_prediction_packet(store, book, packet)

    assert any(Path(p).name == "prediction-prompt-latest.md" for p in paths)
    prompt_latest = store / book / "predictions" / "prediction-prompt-latest.md"
    assert prompt_latest.exists()
    assert "小说预测分析请求" in prompt_latest.read_text(encoding="utf-8")


def test_predict_llm_parses_claude_json_predictions(tmp_path: Path, monkeypatch):
    store, book = import_book(tmp_path)
    llm_payload = {
        "result": json.dumps(
            {
                "predictions": [
                    {
                        "id": "P1",
                        "type": "plot_direction",
                        "claim": "主线冲突会继续升级。",
                        "probability": "high",
                        "confidence": 0.77,
                        "reasoning": ["证据显示冲突尚未解决。"],
                        "supporting_evidence": ["c0001-001"],
                        "counter_evidence": [],
                        "risk": "证据仍有限。",
                    }
                ],
                "alternative_scenarios": [],
                "watchlist": [],
            },
            ensure_ascii=False,
        )
    }

    def fake_run(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(llm_payload, ensure_ascii=False), stderr="")

    monkeypatch.setattr(predictor.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(predictor.subprocess, "run", fake_run)
    args = SimpleNamespace(
        question="后续剧情可能怎么发展？",
        scope="general",
        horizon="next-arc",
        anchor_chapter=None,
        anchor_chunk=None,
        context_chunks=5,
        top=8,
        semantic=False,
    )

    packet = predictor.build_prediction_packet(store, book, args, use_llm=True)

    assert packet["predictions"][0]["source"] == "llm"
    assert packet["predictions"][0]["claim"] == "主线冲突会继续升级。"
    assert packet["predictions"][0]["confidence"] == 0.77


def test_predict_write_documents_are_visible_in_web_documents(tmp_path: Path, monkeypatch):
    store, book = import_book(tmp_path)
    run_cli(store, "predict", book, "后续剧情可能怎么发展？", "--write", "--json")
    monkeypatch.setenv("NOVEL_READER_HOME", str(store))
    app = make_app()
    client = app.test_client()

    response = client.get(f"/api/documents?book={book}")

    assert response.status_code == 200
    paths = {item["path"] for item in response.get_json()["documents"]}
    assert "predictions/prediction-latest.md" in paths
    assert "predictions/prediction-latest.json" in paths


def test_predict_full_scope_requires_finalized_balanced_session(tmp_path: Path):
    store, book = import_book(tmp_path)
    session = complete_balanced_session(store, book)

    blocked = run_cli(store, "predict", book, "--scope-mode", "full", "--json", check=False)
    assert blocked.returncode != 0
    payload = json.loads(blocked.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "FULL_SCOPE_NOT_ALLOWED"

    run_cli(store, "finalize-reading", session["session_id"], "--json")
    allowed = load_json(run_cli(store, "predict", book, "--scope-mode", "full", "--json"))
    assert allowed["ok"] is True
    assert allowed["predictions"]


def test_predict_full_scope_uses_specified_session_id(tmp_path: Path):
    store, book = import_book(tmp_path)
    finalized = complete_balanced_session(store, book)
    run_cli(store, "finalize-reading", finalized["session_id"], "--json")
    newer_unfinalized = load_json(run_cli(store, "read-session", book, "--mode", "balanced", "--json"))

    allowed = run_cli(
        store,
        "predict",
        book,
        "--scope-mode",
        "full",
        "--session-id",
        finalized["session_id"],
        "--json",
    )

    payload = json.loads(allowed.stdout)
    assert payload["ok"] is True
    assert payload["predictions"]

    blocked = run_cli(
        store,
        "predict",
        book,
        "--scope-mode",
        "full",
        "--session-id",
        newer_unfinalized["session_id"],
        "--json",
        check=False,
    )
    assert blocked.returncode != 0


def test_predict_semantic_warns_that_prediction_uses_local_heuristics(tmp_path: Path):
    store, book = import_book(tmp_path)

    data = load_json(run_cli(store, "predict", book, "后续剧情可能怎么发展？", "--semantic", "--json"))

    assert data["prediction_goal"]["semantic"] is True
    assert data["prediction_goal"]["semantic_requested"] is True
    assert data["prediction_goal"]["semantic_applied"] is False
    assert any("semantic" in item and "local heuristic" in item for item in data["warnings"])


def test_predict_without_semantic_reports_applied_false(tmp_path: Path):
    store, book = import_book(tmp_path)

    data = load_json(run_cli(store, "predict", book, "后续剧情可能怎么发展？", "--json"))

    assert data["prediction_goal"]["semantic"] is False
    assert data["prediction_goal"]["semantic_requested"] is False
    assert data["prediction_goal"]["semantic_applied"] is False


def test_web_predict_api_partial_and_full_scope_guard(tmp_path: Path, monkeypatch):
    store, book = import_book(tmp_path)
    monkeypatch.setenv("NOVEL_READER_HOME", str(store))
    monkeypatch.setenv("NOVEL_READER_WEB_TOKEN", "test-token")
    session = load_json(run_cli(store, "read-session", book, "--mode", "balanced", "--json"))
    app = make_app()
    client = app.test_client()
    headers = csrf_headers(app)

    partial = client.post(
        "/api/predict",
        json={"book": book, "question": "后续剧情可能怎么发展？"},
        headers=headers,
    )
    assert partial.status_code == 200
    assert partial.get_json()["ok"] is True

    blocked = client.post(
        "/api/predict",
        json={"book": book, "scope_mode": "full", "session_id": session["session_id"]},
        headers=headers,
    )
    assert blocked.status_code == 400
    assert blocked.get_json()["ok"] is False
