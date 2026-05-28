import json
from types import SimpleNamespace

from novel_reader import web_app


def test_call_claude_sends_prompt_over_stdin(monkeypatch):
    long_prompt = "x" * 200_000
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )

    monkeypatch.setattr(web_app.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(web_app.subprocess, "run", fake_run)

    result = web_app.call_claude(long_prompt, "once", "normal")

    assert result["reply"] == "ok"
    assert long_prompt not in captured["args"]
    assert captured["kwargs"]["input"] == long_prompt
    assert captured["kwargs"]["text"] is True
    assert "-p" in captured["args"]


def test_call_claude_extracts_usage_and_cache(monkeypatch):
    payload = {
        "result": "ok",
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 120,
            "cache_creation_input_tokens": 400,
            "cache_read_input_tokens": 600,
        },
    }

    def fake_run(args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(web_app.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(web_app.subprocess, "run", fake_run)

    result = web_app.call_claude("prompt", "continue", "normal")

    assert result["reply"] == "ok"
    assert result["usage"]["input_tokens"] == 1000
    assert result["cache"]["available"] is True
    assert result["cache"]["read_input_tokens"] == 600
    assert result["cache"]["creation_input_tokens"] == 400
    assert result["cache"]["hit_rate"] == 0.6
    assert result["raw_parsed"] == payload


def test_call_claude_without_usage_returns_unavailable_cache(monkeypatch):
    def fake_run(args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"result": "ok"}),
            stderr="",
        )

    monkeypatch.setattr(web_app.shutil, "which", lambda name: "claude")
    monkeypatch.setattr(web_app.subprocess, "run", fake_run)

    result = web_app.call_claude("prompt", "once", "normal")

    assert result["reply"] == "ok"
    assert result["usage"] == {}
    assert result["cache"]["available"] is False


def test_build_claude_prompt_truncates_oversized_message():
    prompt = web_app.build_claude_prompt("x" * 40_000, "book-1", {})

    assert len(prompt) < 25_000
    assert "[message truncated]" in prompt
