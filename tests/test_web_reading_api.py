import os
from pathlib import Path

from novel_reader.web_app import make_app

from test_reading_session import import_book, valid_l1_note


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
