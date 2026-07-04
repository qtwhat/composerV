"""Sentence-level transcription via MLX Whisper (Metal, on-device, fast on Apple Silicon).

VAD says "voice here"; Whisper says WHICH sentence and exactly where it starts/ends. The
montage uses these precise sentence boundaries so it can let a person finish a sentence to
the word (not just to the next silence). Model: mlx-community/whisper-large-v3-turbo (cached
locally; loaded offline). sentences_from_words is pure (the testable boundary logic)."""

from __future__ import annotations

import os
from collections.abc import Sequence

_MODEL = "mlx-community/whisper-large-v3-turbo"
_ENDERS = ".!?。！？…"


def sentences_from_words(
    words: Sequence[dict],
    enders: str = _ENDERS,
    max_gap_s: float = 0.6,
) -> list[tuple[float, float, str]]:
    """Group Whisper word objects ({word,start,end}) into sentences. A boundary is either
    terminal punctuation OR a pause longer than max_gap_s before the next word. The pause rule
    is what makes this work on languages Whisper returns without sentence punctuation (notably
    Chinese): otherwise a whole monologue with no '。' comes back as one multi-minute "sentence".
    Returns [(start_s, end_s, text)] using the first/last word timestamps of each sentence."""
    sents: list[tuple[float, float, str]] = []
    cur: list[dict] = []
    for i, w in enumerate(words):
        cur.append(w)
        t = (w.get("word") or "").strip()
        ends_on_punct = bool(t) and t[-1] in enders
        gap_break = False
        if i + 1 < len(words):
            try:
                gap_break = float(words[i + 1]["start"]) - float(w["end"]) > max_gap_s
            except (KeyError, TypeError, ValueError):
                gap_break = False
        if ends_on_punct or gap_break:
            made = _make(cur)
            if made:
                sents.append(made)
            cur = []
    made = _make(cur)
    if made:
        sents.append(made)
    return sents


def _make(words: list[dict]) -> tuple[float, float, str] | None:
    if not words:
        return None
    text = "".join(w.get("word", "") for w in words).strip()
    if not text:
        return None
    return (float(words[0]["start"]), float(words[-1]["end"]), text)


def transcribe_sentences(path: str, model: str = _MODEL) -> list[tuple[float, float, str]]:
    """Transcribe `path` and return precise sentence segments [(start_s, end_s, text)].
    Empty list if there's no speech / the model can't run. Loads the cached model offline."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")  # the model is vendored in the HF cache
    try:
        import mlx_whisper

        r = mlx_whisper.transcribe(path, path_or_hf_repo=model, word_timestamps=True)
    except Exception:
        return []
    words = [w for seg in r.get("segments", []) for w in (seg.get("words") or [])]
    if words:
        return sentences_from_words(words)
    # no word timestamps -> fall back to Whisper's own segment boundaries
    return [(float(s["start"]), float(s["end"]), s["text"].strip())
            for s in r.get("segments", []) if s.get("text", "").strip()]
