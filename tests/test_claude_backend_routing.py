"""claude_text backend routing (CLI vs Anthropic API) + api_text's ""-on-failure contract."""

import types

import pytest

from composerv.analyze.backends import claude_api, claude_cli


class _FakeMessages:
    def __init__(self, blocks):
        self._blocks = blocks
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return types.SimpleNamespace(content=self._blocks)


def _fake_client(blocks):
    return types.SimpleNamespace(messages=_FakeMessages(blocks))


def test_api_text_joins_text_blocks():
    client = _fake_client([
        types.SimpleNamespace(type="text", text="hello "),
        types.SimpleNamespace(type="tool_use", text="IGNORED"),
        types.SimpleNamespace(type="text", text="world"),
    ])
    out = claude_api.api_text("hi", model="claude-opus-4-6", _client=client)
    assert out == "hello world"
    assert client.messages.last_kwargs["model"] == "claude-opus-4-6"
    assert client.messages.last_kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_api_text_returns_empty_on_failure(capsys):
    class Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("nope")

    assert claude_api.api_text("hi", _client=Boom()) == ""
    assert "api_text" in capsys.readouterr().err


def test_claude_text_falls_back_to_api_without_cli(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CV_CLAUDE_BACKEND", raising=False)
    calls = {}
    monkeypatch.setattr(claude_api, "api_text",
                        lambda prompt, model, timeout: (calls.setdefault("args", (prompt, model)), "reply")[1])
    assert claude_cli.claude_text("p", model="claude-opus-4-6") == "reply"
    assert calls["args"] == ("p", "claude-opus-4-6")


def test_claude_text_env_forces_api_over_cli(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CV_CLAUDE_BACKEND", "api")
    monkeypatch.setattr(claude_api, "api_text", lambda prompt, model, timeout: "via-api")
    assert claude_cli.claude_text("p") == "via-api"


def test_claude_text_forced_api_without_key_fails_loud(monkeypatch, capsys):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CV_CLAUDE_BACKEND", "api")
    assert claude_cli.claude_text("p") == ""
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_claude_text_nothing_available_fails_loud(monkeypatch, capsys):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CV_CLAUDE_BACKEND", raising=False)
    assert claude_cli.claude_text("p") == ""
    err = capsys.readouterr().err
    assert "no `claude` CLI" in err and "ANTHROPIC_API_KEY" in err
