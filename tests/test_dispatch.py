"""Tests for cli.dispatch_command.

Verifies that dispatch_command correctly handles the three return types
(dict / int / None) plus the NovelReaderJsonError path, so that command_*
functions can return structured data instead of printing to stdout.
"""
import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_reader import cli


def _ns(func, **kwargs):
    """Build a Namespace with a callable `func` and arbitrary kwargs."""
    ns = SimpleNamespace(**kwargs)
    ns.func = func
    return ns


def test_dispatch_dict_return_prints_json_and_returns_zero(capsys):
    """command_* returning a dict → dispatch prints it as JSON and returns 0."""

    def fake_command(args):
        return {"ok": True, "data": [1, 2, 3]}

    args = _ns(fake_command)
    rc = cli.dispatch_command(args)

    captured = capsys.readouterr()
    assert rc == 0
    import json

    parsed = json.loads(captured.out)
    assert parsed == {"ok": True, "data": [1, 2, 3]}


def test_dispatch_int_return_passes_through(capsys):
    """command_* returning an int (exit code) → dispatch returns it, prints nothing."""

    def fake_command(args):
        return 2

    args = _ns(fake_command)
    rc = cli.dispatch_command(args)

    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""


def test_dispatch_none_return_treated_as_success(capsys):
    """command_* returning None → dispatch returns 0, prints nothing."""

    def fake_command(args):
        return None

    args = _ns(fake_command)
    rc = cli.dispatch_command(args)

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""


def test_dispatch_zero_int_return(capsys):
    """command_* returning 0 → dispatch returns 0, prints nothing."""

    def fake_command(args):
        return 0

    args = _ns(fake_command)
    rc = cli.dispatch_command(args)

    assert rc == 0
    assert capsys.readouterr().out == ""


def test_dispatch_novel_reader_json_error_propagates(capsys):
    """NovelReaderJsonError raised by command_* propagates out of dispatch
    (main's except branch handles it). dispatch itself does NOT catch it.
    """

    def fake_command(args):
        raise cli.NovelReaderJsonError({"ok": False, "error": "guarded"}, return_code=2)

    args = _ns(fake_command)
    with pytest.raises(cli.NovelReaderJsonError) as exc_info:
        cli.dispatch_command(args)

    assert exc_info.value.return_code == 2
    assert exc_info.value.payload == {"ok": False, "error": "guarded"}


def test_dispatch_novel_reader_error_propagates(capsys):
    """NovelReaderError raised by command_* propagates out of dispatch
    (main's except branch handles it).
    """

    def fake_command(args):
        raise cli.NovelReaderError("book not found")

    args = _ns(fake_command)
    with pytest.raises(cli.NovelReaderError):
        cli.dispatch_command(args)


def test_main_translates_novel_reader_json_error_to_json_stdout(capsys):
    """End-to-end: main catches NovelReaderJsonError and prints payload as JSON."""

    def fake_command(args):
        raise cli.NovelReaderJsonError({"ok": False, "reason": "scope"}, return_code=2)

    # Build a minimal parser that binds fake_command, then call main.
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("fake")
    p.set_defaults(func=fake_command)
    # Monkeypatch build_parser to return our minimal parser.
    original_build = cli.build_parser
    cli.build_parser = lambda: parser
    try:
        rc = cli.main(["fake"])
    finally:
        cli.build_parser = original_build

    captured = capsys.readouterr()
    import json

    parsed = json.loads(captured.out)
    assert rc == 2
    assert parsed == {"ok": False, "reason": "scope"}


def test_dispatch_dict_preserves_chinese_text(capsys):
    """dict with Chinese values must be printed without mojibake."""

    def fake_command(args):
        return {"ok": True, "title": "验收书", "提示": "当前产物"}

    args = _ns(fake_command)
    rc = cli.dispatch_command(args)

    captured = capsys.readouterr()
    assert rc == 0
    assert "验收书" in captured.out
    assert "当前产物" in captured.out
