"""Text-only Claude via the Anthropic API (ANTHROPIC_API_KEY).

The path for third parties without a Claude Code subscription: `claude_text` in
claude_cli.py prefers the `claude` CLI when installed, and falls back here when it
is absent (or when CV_CLAUDE_BACKEND=api forces the API). Needs the `analyze-api`
extra (`anthropic`).
"""

from __future__ import annotations

import sys


def api_text(prompt: str, model: str = "claude-sonnet-4-6", timeout: int = 300,
             max_tokens: int = 8192, _client=None) -> str:
    """One text-in / text-out API call. Returns "" on any failure with a stderr note
    (the same contract as claude_text: callers treat "" as a failed call, not empty).

    `_client` is injectable for tests (music/mood.py's injectable-run pattern).
    """
    try:
        if _client is None:
            import anthropic

            _client = anthropic.Anthropic(timeout=float(timeout))
        msg = _client.messages.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception as e:  # ""-on-failure contract: never raise into the pipeline
        print(f"[api_text] call failed: {type(e).__name__}: {e}", file=sys.stderr)
        return ""
