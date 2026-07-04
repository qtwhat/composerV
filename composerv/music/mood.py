"""Infer the feeling of a set of clips (so the app can suggest fitting music).

build_feeling_prompt / parse_feeling are pure; infer_feeling takes an injectable run.
"""

from __future__ import annotations

from collections.abc import Callable

FEELINGS = ["upbeat", "calm", "nostalgic", "sad"]


def build_feeling_prompt(summaries: list[str]) -> str:
    listing = "\n".join(f"- {s}" for s in summaries if s)
    return (
        "Here are short descriptions of clips from one set of personal footage:\n\n"
        f"{listing}\n\n"
        "What single overall FEELING best fits a montage of these clips? Choose exactly one of: "
        f"{', '.join(FEELINGS)}. Reply with only that one word."
    )


def parse_feeling(text: str) -> str:
    low = (text or "").lower()
    for f in FEELINGS:
        if f in low:
            return f
    return "calm"


def _default_run(prompt: str) -> str:
    from composerv.analyze.backends.claude_cli import claude_text

    return claude_text(prompt)


def infer_feeling(summaries: list[str], run: Callable[[str], str] | None = None) -> str:
    run = run or _default_run
    return parse_feeling(run(build_feeling_prompt(summaries)))
