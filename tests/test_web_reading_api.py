import os
from pathlib import Path

from novel_reader.web_app import make_app

from test_reading_session import import_book, load_json, run_cli, valid_l1_note, valid_l2_note


def complete_session_notes(store: Path, session: dict) -> None:
    for chapter in range(1, 5):
        packet = load_json(run_cli(store, "read-next", session["session_id"], "--chapter", str(chapter), "--json"))
        chunks = [item["chunk_id"] for item in packet["chapters"][0]["chunks"]]
        required = packet["chapters"][0]["required_level"]
        note = valid_l2_note(chunks) if required == "L2_READ" else valid_l1_note(chunks[0])
        run_cli(store, "submit-note", session["session_id"], "--chapter", str(chapter), "--text", note, "--json")


def test_web_reading_session_flow(tmp_path: Path, monkeypatch):
    store, book = import_book(tmp_path)
    monkeypatch.setenv("NOVEL_READER_HOME", str(store))
    monkeypatch.setenv("NOVEL_READER_WEB_TOKEN", "test-token")
    app = make_app()
    client = app.test_client()
    headers = {"X-Novel-Reader-Token": "test-token"}

    created = client.post(
        "/api/reading/session",
        json={"book": book, "goal": "full", "mode": "survey", "deep_ratio": 0.25},
        headers=headers,
    )
    assert created.status_code == 200
    session = created.get_json()
    assert session["ok"] is True
    session_id = session["session_id"]

    status = client.get(f"/api/reading/session/{session_id}/status")
    assert status.status_code == 200
    assert status.get_json()["l1_coverage_percent"] == 0

    next_response = client.post(
        f"/api/reading/session/{session_id}/next",
        json={"batch_chapters": 1},
        headers=headers,
    )
    assert next_response.status_code == 200
    packet = next_response.get_json()
    assert packet["next_allowed_action"] == "submit-note"
    chunk_id = packet["chapters"][0]["chunks"][0]["chunk_id"]

    submitted = client.post(
        f"/api/reading/session/{session_id}/submit-note",
        json={"chapter": 1, "text": valid_l1_note(chunk_id), "level": "L1_SKIMMED"},
        headers=headers,
    )
    assert submitted.status_code == 200
    assert submitted.get_json()["ok"] is True

    finalized = client.post(f"/api/reading/session/{session_id}/finalize", json={}, headers=headers)
    assert finalized.status_code == 400
    assert finalized.get_json()["ok"] is False

    assert os.environ["NOVEL_READER_HOME"] == str(store)


def test_web_old_action_analyze_uses_full_scope_guard(tmp_path: Path, monkeypatch):
    store, book = import_book(tmp_path)
    monkeypatch.setenv("NOVEL_READER_HOME", str(store))
    monkeypatch.setenv("NOVEL_READER_WEB_TOKEN", "test-token")
    session = load_json(run_cli(store, "read-session", book, "--mode", "balanced", "--deep-ratio", "0.5", "--json"))
    complete_session_notes(store, session)
    app = make_app()
    client = app.test_client()
    headers = {"X-Novel-Reader-Token": "test-token"}

    blocked = client.post(
        "/api/action/analyze",
        json={"book": book, "scope": "full", "session_id": session["session_id"]},
        headers=headers,
    )
    assert blocked.status_code == 400
    payload = blocked.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "FULL_SCOPE_NOT_ALLOWED"

    partial = client.post("/api/action/analyze", json={"book": book}, headers=headers)
    assert partial.status_code == 200
    assert partial.get_json()["ok"] is True


def test_web_style_uses_full_scope_guard(tmp_path: Path, monkeypatch):
    store, book = import_book(tmp_path)
    monkeypatch.setenv("NOVEL_READER_HOME", str(store))
    monkeypatch.setenv("NOVEL_READER_WEB_TOKEN", "test-token")
    session = load_json(run_cli(store, "read-session", book, "--mode", "balanced", "--deep-ratio", "0.5", "--json"))
    complete_session_notes(store, session)
    app = make_app()
    client = app.test_client()
    headers = {"X-Novel-Reader-Token": "test-token"}

    blocked = client.post(
        "/api/style",
        json={"book": book, "scope": "full", "session_id": session["session_id"], "json": True},
        headers=headers,
    )
    assert blocked.status_code == 400
    assert blocked.get_json()["error"]["code"] == "FULL_SCOPE_NOT_ALLOWED"

    partial = client.post("/api/style", json={"book": book, "json": True}, headers=headers)
    assert partial.status_code == 200
    assert partial.get_json()["ok"] is True


def test_web_continue_uses_full_scope_guard(tmp_path: Path, monkeypatch):
    store, book = import_book(tmp_path)
    monkeypatch.setenv("NOVEL_READER_HOME", str(store))
    monkeypatch.setenv("NOVEL_READER_WEB_TOKEN", "test-token")
    session = load_json(run_cli(store, "read-session", book, "--mode", "deep", "--after-chapter", "3", "--json"))
    app = make_app()
    client = app.test_client()
    headers = {"X-Novel-Reader-Token": "test-token"}

    blocked = client.post(
        "/api/continue",
        json={"book": book, "after_chapter": 3, "scope": "full", "session_id": session["session_id"]},
        headers=headers,
    )
    assert blocked.status_code == 400
    payload = blocked.get_json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "FULL_SCOPE_NOT_ALLOWED"

    partial = client.post("/api/continue", json={"book": book, "after_chapter": 3}, headers=headers)
    assert partial.status_code == 200
    assert partial.get_json()["ok"] is True
