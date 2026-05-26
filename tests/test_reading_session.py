import json
import math
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "novel-reader"


def run_cli(store: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(CLI), "--store", str(store), *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        env=env,
        capture_output=True,
        check=check,
    )


def load_json(result: subprocess.CompletedProcess[str]) -> dict:
    text = result.stdout.strip() or result.stderr.strip()
    return json.loads(text)


def make_book(path: Path, chapters: int = 4) -> None:
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
    path.write_text("\n".join(parts), encoding="utf-8")


def import_book(tmp_path: Path, chapters: int = 4) -> tuple[Path, str]:
    store = tmp_path / "store"
    source = tmp_path / "book.md"
    make_book(source, chapters=chapters)
    run_cli(store, "ingest", str(source), "--book-id", "session-book", "--title", "Session Book", "--chunk-chars", "900")
    return store, "session-book"


def valid_l1_note(chunk_id: str) -> str:
    return (
        "one_sentence: The chapter advances the hero's immediate goal while planting conflict. "
        "events: The hero enters the target place, meets resistance, and discovers a clue. "
        "characters: Hero wants progress; Alice and Bob create pressure around the choice. "
        f"evidence_chunks: {chunk_id}. "
        + "This L1 note is intentionally long enough to pass the minimum length gate. " * 3
    )


def valid_l2_note(chunk_ids: list[str]) -> str:
    evidence = ", ".join(chunk_ids[: max(3, min(3, len(chunk_ids)))])
    return "\n".join(
        [
            "事件: The chapter moves through arrival, confrontation, clue discovery, and temporary resolution.",
            "人物与动机: The hero wants to continue the mission while Alice and Bob test loyalty and risk.",
            "冲突: External guards and internal doubt force a costly choice.",
            "情节因果: The discovered clue explains why the next chapter can escalate the tower conflict.",
            "伏笔/回收: The secret and betrayal language point to later truth revelation.",
            "设定/地点/势力: Tower, Empire, rule, faction, and oath define the local power structure.",
            "时间线: This chapter occurs after the prior arrival and before the next confrontation.",
            "写作观察: The scene uses repeated action words and explanatory setting terms.",
            f"证据块: {evidence}.",
            "Extra detail. " * 40,
        ]
    )


def test_read_session_status_and_read_next(tmp_path: Path):
    store, book = import_book(tmp_path)
    session = load_json(run_cli(store, "read-session", book, "--mode", "survey", "--json"))

    assert session["ok"] is True
    assert session["chapter_count"] == 4
    assert session["next_chapter"] == 1

    status = load_json(run_cli(store, "reading-status", session["session_id"], "--json"))
    assert status["l1_coverage_percent"] == 0
    assert status["completed_chapters"] == 0
    assert status["final_reports_allowed"] is False

    packet = load_json(run_cli(store, "read-next", session["session_id"], "--json"))
    assert packet["next_allowed_action"] == "submit-note"
    assert packet["chapters"][0]["chapter_index"] == 1
    assert packet["chapters"][0]["required_level"] == "L1_SKIMMED"
    assert packet["note_schema"]["level"] == "L1_SKIMMED"


def test_submit_note_validation_failures_do_not_update_coverage(tmp_path: Path):
    store, book = import_book(tmp_path)
    session = load_json(run_cli(store, "read-session", book, "--mode", "survey", "--json"))
    session_id = session["session_id"]
    read_packet = load_json(run_cli(store, "read-next", session_id, "--json"))
    chunk_id = read_packet["chapters"][0]["chunks"][0]["chunk_id"]

    empty = run_cli(store, "submit-note", session_id, "--chapter", "1", "--text", "", "--json", check=False)
    assert empty.returncode != 0
    assert load_json(empty)["ok"] is False

    missing_fields = run_cli(store, "submit-note", session_id, "--chapter", "1", "--text", "only words " * 80, "--json", check=False)
    assert missing_fields.returncode != 0
    assert "missing_fields" in load_json(missing_fields)["quality"]

    no_chunk = run_cli(store, "submit-note", session_id, "--chapter", "1", "--text", valid_l1_note("c9999-999"), "--json", check=False)
    assert no_chunk.returncode != 0
    assert "missing_chunk_refs" in load_json(no_chunk)["quality"]

    status = load_json(run_cli(store, "reading-status", session_id, "--json"))
    assert status["l1_coverage_percent"] == 0
    assert chunk_id


def test_submit_valid_l1_note_updates_session_and_legacy_summary(tmp_path: Path):
    store, book = import_book(tmp_path)
    session_id = load_json(run_cli(store, "read-session", book, "--mode", "survey", "--json"))["session_id"]
    read_packet = load_json(run_cli(store, "read-next", session_id, "--json"))
    chunk_id = read_packet["chapters"][0]["chunks"][0]["chunk_id"]

    result = load_json(run_cli(store, "submit-note", session_id, "--chapter", "1", "--text", valid_l1_note(chunk_id), "--json"))
    assert result["ok"] is True
    assert result["coverage_level"] == "L1_SKIMMED"

    status = load_json(run_cli(store, "reading-status", session_id, "--json"))
    assert status["completed_chapters"] == 1
    assert status["l1_coverage_percent"] == 25

    legacy = load_json(run_cli(store, "status", book, "--json"))
    assert legacy["summary_coverage"]["summarized_chapters"] == 1


def test_balanced_and_full_scope_guards(tmp_path: Path):
    store, book = import_book(tmp_path)
    session = load_json(run_cli(store, "read-session", book, "--mode", "balanced", "--deep-ratio", "0.5", "--json"))
    assert len(session["key_chapters"]) == 2

    blocked_outline = run_cli(store, "outline", book, "--scope", "full", "--json", check=False)
    assert blocked_outline.returncode != 0
    assert load_json(blocked_outline)["ok"] is False

    blocked_analyze = run_cli(store, "analyze", book, "--scope", "full", check=False)
    assert blocked_analyze.returncode != 0

    for chapter in range(1, 5):
        packet = load_json(run_cli(store, "read-next", session["session_id"], "--chapter", str(chapter), "--json"))
        chunks = [item["chunk_id"] for item in packet["chapters"][0]["chunks"]]
        required = packet["chapters"][0]["required_level"]
        note = valid_l2_note(chunks) if required == "L2_READ" else valid_l1_note(chunks[0])
        run_cli(store, "submit-note", session["session_id"], "--chapter", str(chapter), "--text", note, "--json")

    final_status = load_json(run_cli(store, "reading-status", session["session_id"], "--json"))
    assert final_status["final_reports_allowed"] is True

    finalized = load_json(run_cli(store, "finalize-reading", session["session_id"], "--json"))
    assert finalized["final_reports_allowed"] is True

    outline = load_json(run_cli(store, "outline", book, "--scope", "full", "--json"))
    assert outline["ok"] is True

    analyze = load_json(run_cli(store, "analyze", book, "--scope", "full"))
    assert analyze["ok"] is True


def test_read_next_batch_and_continue_scope_guard(tmp_path: Path):
    store, book = import_book(tmp_path)
    session = load_json(run_cli(store, "read-session", book, "--mode", "deep", "--after-chapter", "3", "--json"))
    batch = load_json(run_cli(store, "read-next", session["session_id"], "--batch-chapters", "3", "--json"))
    assert [item["chapter_index"] for item in batch["chapters"]] == [1, 2, 3]
    assert len(session["l3_required"]) < 4

    blocked = run_cli(store, "continue", book, "--after-chapter", "3", "--scope", "full", "--json", check=False)
    assert blocked.returncode != 0
    payload = load_json(blocked)
    assert payload["ok"] is False
    assert "read-next" in payload["next_step"]
