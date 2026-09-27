"""A broken desk module must never stop the authoring commands from parsing."""

from __future__ import annotations

import builtins
import json
from typing import Any

import pytest

from macro_agent import cli


def test_a_desk_import_failure_leaves_author_commands_parsing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_import = builtins.__import__

    def failing_import(name: str, *args: Any, **kwargs: Any) -> Any:
        fromlist = args[2] if len(args) > 2 else kwargs.get("fromlist")
        if name.endswith("desk_cli") or (fromlist and "desk_cli" in fromlist):
            raise ImportError("simulated broken desk module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)
    root = cli.parser()

    author = root.parse_args(["author", "phase-context", "--week", "2026-W40"])
    assert author.command == "author"

    desk = root.parse_args(["desk", "catalysts", "list"])
    assert desk.fn(desk, None) == 3
    reply = json.loads(capsys.readouterr().out)
    assert reply["ok"] is False
    assert reply["error"]["category"] == "desk_unavailable"
