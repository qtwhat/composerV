"""Judge whether a clip's talk is worth remembering, and keep the worthy exchange WHOLE.

A talky clip can hold minutes of speech. There is no single "best line": if a moment is worth
remembering, the whole exchange should be kept, complete; if it is just filler/logistics/ambient
noise, none of it should interrupt the music. That is an emotional judgement, so an LLM makes it.
build_worth_prompt / parse_span are the pure, testable halves; select_memorable_span runs an
injected `run` (Claude by default) and returns the contiguous worthy sentences (or none). When
there is no transcript text (VAD-only) it keeps all detected speech, so a person is never cut
off even without the transcribe extra.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence


def build_worth_prompt(sentences: Sequence[tuple]) -> str:
    """Number the sentences (1-based) with durations; ask for the worthy range, or 'none'."""
    lines = []
    for i, sent in enumerate(sentences, start=1):
        dur = float(sent[1]) - float(sent[0])
        text = (sent[2] if len(sent) > 2 else "").strip()
        lines.append(f"{i}. [{dur:.1f}s] {text}")
    listing = "\n".join(lines)
    return (
        "These are the lines spoken in one clip of family footage, for a family MEMORY reel:\n\n"
        f"{listing}\n\n"
        "From an emotional, keepsake standpoint, decide what is worth remembering:\n"
        "- If there is a meaningful moment or exchange worth reliving (warmth, humour, a "
        "milestone, something that matters), reply with the line-number RANGE to keep, and keep "
        "that exchange COMPLETE — e.g. '2-5' (or a single number like '3').\n"
        "- If it is just filler, logistics, or ambient noise nobody would want to relive, reply "
        "'none'.\n"
        "Reply with ONLY the range or 'none'."
    )


def parse_span(text: str, n: int) -> tuple[int, int] | None:
    """Read the reply as a 1-based inclusive line range; return a 0-based inclusive (lo, hi)
    clamped to [0, n-1]. None if it says no range (e.g. 'none' / no digits)."""
    nums = [int(m.group()) for m in re.finditer(r"\d+", text or "")]
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    lo = max(1, min(lo, n))
    hi = max(1, min(hi, n))
    return (lo - 1, hi - 1)


def _default_run(prompt: str) -> str:
    from composerv.analyze.backends.claude_cli import claude_text

    return claude_text(prompt)


def select_memorable_span(
    sentences: Sequence[tuple],
    *,
    run: Callable[[str], str] | None = None,
) -> list[tuple]:
    """Return the contiguous worthy sentences to keep WHOLE (same tuple shape as the input), or
    [] if nothing is worth keeping. With no transcript text, keeps all detected speech (can't
    judge worth → don't truncate). Empty in -> empty out."""
    sents = list(sentences)
    if not sents:
        return []
    has_text = any(len(s) > 2 and (s[2] or "").strip() for s in sents)
    if not has_text:
        return sents  # VAD-only: keep the speech, don't cut anyone off
    r = run or _default_run
    try:
        span = parse_span(r(build_worth_prompt(sents)), len(sents))
    except Exception:
        span = None
    if span is None:
        return []
    lo, hi = span
    return sents[lo:hi + 1]
