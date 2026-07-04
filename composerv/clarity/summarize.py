"""Turn a clip into a concise "what is this" description for the user.

This is the clarity vehicle (text-first): 1-3 sentences identifying who / doing what /
where, distinctive enough to recognize the clip at a glance. It is deliberately NOT the
per-frame "what happens over time" moment list (that is for editing later, and we found it
repetitive on local models).

Engine is pluggable via `run(prompt, image_paths) -> str`:
- `local_describe` (default): on-device Qwen2.5-VL. Pixels never leave the machine.
- `claude_describe` (refine): higher quality; the clip's frames go to the cloud — only when
  the user explicitly refines one clip.

`build_clarity_prompt` / `parse_clarity` are pure; `summarize_clip` takes an injectable run.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable

from pydantic import BaseModel

from composerv.analyze.clip_video import _matching_brace

_PLACEHOLDER = re.compile(r"^<.*>$")  # an unfilled template value like "<1-3 sentence ...>"


def _usable(value) -> bool:
    v = (str(value) if value is not None else "").strip()
    return bool(v) and not _PLACEHOLDER.match(v)


class ClaritySummary(BaseModel):
    text: str = ""
    source: str = ""   # "local" | "claude"


def build_clarity_prompt(frames: list[tuple[float, str]]) -> str:
    listing = "\n".join(f"{i + 1}. t={t:.1f}s  {p}" for i, (t, p) in enumerate(frames))
    n = len(frames)
    return (
        f"You are given {n} frames sampled in order across one short video clip, each with its "
        "timestamp and file path. Identify WHAT THIS CLIP IS, so a person can recognize it at a "
        "glance: who is in it, what they are doing, and where. Write 1-3 short sentences. Be "
        "concrete and distinctive (what sets this clip apart); do NOT give a frame-by-frame list "
        "and do NOT pad with generic filler.\n\n"
        f"FRAMES (in order):\n{listing}\n\n"
        'Reply with ONLY a JSON object: {"description":"<1-3 sentence what-this-is>"}. '
        "Start with { and end with }."
    )


def parse_clarity(text: str) -> str:
    """Pull the description out of a JSON object; skip unfilled `<...>` placeholders and
    recover truncated JSON; fall back to the cleaned raw text."""
    if not text:
        return ""
    # 1. a well-formed {...} carrying a usable "description"
    i = 0
    while True:
        start = text.find("{", i)
        if start == -1:
            break
        end = _matching_brace(text, start)
        if end == -1:
            break
        try:
            d = json.loads(text[start : end + 1])
            if isinstance(d, dict) and _usable(d.get("description")):
                return str(d["description"]).strip()
        except json.JSONDecodeError:
            pass
        i = start + 1
    # 2. malformed but closed: "description": "...."  (up to the next " },)
    m = re.search(r'"description"\s*:\s*"(.+?)"\s*[},]', text, re.S)
    if m and _usable(m.group(1)):
        return m.group(1).strip()
    # 3. truncated (token cap hit before the closing quote)
    m = re.search(r'"description"\s*:\s*"(.+)', text, re.S)
    if m:
        v = m.group(1).strip().rstrip('"').strip()
        if _usable(v):
            return v
    # 4. plain prose answer (no JSON wrapper). If it still looks like (failed) JSON
    #    with no usable description, treat it as no-description rather than surface braces.
    raw = text.strip()
    return "" if raw.startswith(("{", "[")) else raw


def local_describe(prompt: str, image_paths: list[str]) -> str:
    from composerv.analyze.backends.qwen_mlx import DEFAULT_MODEL, _run_generate

    return _run_generate(image_paths, prompt, DEFAULT_MODEL, max_tokens=400, timeout=900)


def claude_describe(prompt: str, image_paths: list[str]) -> str:
    # Claude reads the frame files referenced (by path) in the prompt via its Read tool.
    from composerv.analyze.backends.claude_cli import claude_read

    return claude_read(prompt)


def summarize_clip(
    proxy_path: str,
    duration_s: float,
    run: Callable[[str, list[str]], str] | None = None,
    source: str = "local",
    target_frames: int = 12,
    frames_dir: str | None = None,
) -> ClaritySummary:
    """Sample frames across the clip and produce a concise 'what is this' description."""
    from composerv.index.frames import sample_frames

    run = run or local_describe
    frames_dir = frames_dir or tempfile.mkdtemp(prefix="cv_clarity_")
    fps = min(1.0, target_frames / duration_s) if duration_s and duration_s > 0 else 1.0
    frames = sample_frames(proxy_path, frames_dir, fps=fps)
    specs = [(f.src_pts_s, f.image_path) for f in frames]
    prompt = build_clarity_prompt(specs)
    text = run(prompt, [p for _t, p in specs])
    return ClaritySummary(text=parse_clarity(text), source=source)
