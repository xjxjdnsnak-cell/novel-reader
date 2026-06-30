"""Tests for `novel-reader doctor`.

doctor 必须永不崩溃：每一项失败应作为单条 check 返回，而不是抛异常。
"""
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "novel-reader"


def run_cli(store: Path, *args: str) -> subprocess.CompletedProcess[str]:
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


def load_json(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def test_doctor_returns_json_with_checks(tmp_path: Path):
    store = tmp_path / "store"
    data = load_json(run_cli(store, "doctor", "--json"))

    assert "ok" in data
    assert "checks" in data
    assert isinstance(data["checks"], list)
    assert len(data["checks"]) >= 6

    names = {c["name"] for c in data["checks"]}
    # Core checks must always be present.
    assert "python_version" in names
    assert "store_writable" in names
    assert "sqlite_fts5" in names
    assert "flask_installed" in names
    assert "claude_cli" in names
    assert "embedding_dependencies" in names


def test_doctor_check_shape(tmp_path: Path):
    store = tmp_path / "store"
    data = load_json(run_cli(store, "doctor", "--json"))

    for check in data["checks"]:
        assert "name" in check
        assert "ok" in check
        assert isinstance(check["ok"], bool)
        assert "message" in check
        assert check["severity"] in {"info", "warn", "error"}


def test_doctor_store_writable_passes_when_writable(tmp_path: Path):
    store = tmp_path / "store"
    store.mkdir(parents=True, exist_ok=True)
    data = load_json(run_cli(store, "doctor", "--json"))

    check = next(c for c in data["checks"] if c["name"] == "store_writable")
    assert check["ok"] is True
    assert check["severity"] == "info"


def test_doctor_claude_missing_is_warn_not_error(tmp_path: Path, monkeypatch):
    """缺少 claude CLI 应只 warn，不应让 doctor 整体失败。"""
    store = tmp_path / "store"
    # Force PATH to a minimal location that does not contain claude.
    env = os.environ.copy()
    env["PATH"] = "/usr/bin:/bin"
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(CLI), "--store", str(store), "doctor", "--json"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        env=env,
        capture_output=True,
        check=True,
    )
    data = json.loads(result.stdout)
    check = next(c for c in data["checks"] if c["name"] == "claude_cli")
    assert check["severity"] == "warn"
    assert check["ok"] is False


def test_doctor_overall_ok_when_store_writable_and_fts5(tmp_path: Path):
    store = tmp_path / "store"
    store.mkdir(parents=True, exist_ok=True)
    data = load_json(run_cli(store, "doctor", "--json"))

    # store_writable and sqlite_fts5 are the only "error" severity items.
    # If both pass, overall ok should be True regardless of claude/embedding warns.
    store_check = next(c for c in data["checks"] if c["name"] == "store_writable")
    fts5_check = next(c for c in data["checks"] if c["name"] == "sqlite_fts5")
    if store_check["ok"] and fts5_check["ok"]:
        assert data["ok"] is True


def test_doctor_text_mode_does_not_crash(tmp_path: Path):
    """非 --json 模式应输出人类可读文本，并以 0 退出。"""
    store = tmp_path / "store"
    result = run_cli(store, "doctor")
    assert result.returncode == 0
    # Should mention each check name in the text output.
    assert "python_version" in result.stdout or "Python" in result.stdout
