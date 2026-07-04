"""Clip-level video understanding from a full-coverage frame sequence.

Claude cannot ingest an mp4 directly, so we sample frames across the WHOLE clip (not just
the start), feed them IN ORDER with their timestamps in one call, and have the model reason
about what HAPPENS over time. Every reported moment is grounded to a real sampled
timestamp, so the tool never asserts content at a time it did not look at. (A true
per-frame video model, local Qwen3-VL, is the deeper option.)

build_video_prompt / parse_understanding are pure; understand_clip takes an injectable run.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable

from pydantic import BaseModel, Field


class GroundedObject(BaseModel):
    label: str                 # what it is ("person", "dog", "sign", ...)
    box: list[float]           # [x1, y1, x2, y2]; normalized [0,1] once through normalize_objects


class ClipMoment(BaseModel):
    t: float          # a real sampled timestamp (seconds into the clip)
    text: str         # what is happening at that time
    ocr: str = ""     # readable on-screen text at that time (signs/captions/place names), if any
    objects: list[GroundedObject] = Field(default_factory=list)  # grounded subjects (normalized boxes)


class ClipUnderstanding(BaseModel):
    summary: str = ""          # what happens across the whole clip, over time
    moments: list[ClipMoment] = Field(default_factory=list)


def build_video_prompt(frames: list[tuple[float, str]]) -> str:
    listing = "\n".join(f"{i + 1}. t={t:.1f}s  {p}" for i, (t, p) in enumerate(frames))
    n = len(frames)
    return (
        f"You are given {n} frames sampled IN ORDER across a single short video clip, each with "
        "its timestamp. Read ALL of them in order with the Read tool, then reason about what "
        "HAPPENS over time: actions, movement, events, how the scene and subjects change. Do not "
        "describe a single frame in isolation.\n\n"
        f"FRAMES (in order):\n{listing}\n\n"
        "Cover the WHOLE clip from first to last frame, not just the start. Each moment's t MUST "
        "be one of the timestamps listed above (so you only claim what you actually saw).\n"
        'Reply with ONLY a JSON object: {"summary":"<2-4 sentences on what happens across the '
        'whole clip over time>", "moments":[{"t":<one listed timestamp>,"happening":"<what is '
        'happening then>"}]}. Start your reply with { and end with } — no other text.'
    )


def _matching(text: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index of the bracket that closes the one at `start`, respecting string literals."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return -1


def _matching_brace(text: str, start: int) -> int:
    """Index of the } closing the { at `start`. Kept for callers that import it (clarity.summarize)."""
    return _matching(text, start, "{", "}")


def _extract_obj(text: str, keys: tuple[str, ...] = ("summary", "moments")) -> dict | None:
    """First balanced {...} that parses to a dict containing any of `keys`."""
    i = 0
    while True:
        start = text.find("{", i)
        if start == -1:
            return None
        end = _matching(text, start, "{", "}")
        if end == -1:
            return None
        try:
            d = json.loads(text[start : end + 1])
            if isinstance(d, dict) and any(k in d for k in keys):
                return d
        except json.JSONDecodeError:
            pass
        i = start + 1


def _extract_array(text: str) -> list | None:
    """First balanced [...] that parses to a JSON list."""
    i = 0
    while True:
        start = text.find("[", i)
        if start == -1:
            return None
        end = _matching(text, start, "[", "]")
        if end == -1:
            return None
        try:
            v = json.loads(text[start : end + 1])
            if isinstance(v, list):
                return v
        except json.JSONDecodeError:
            pass
        i = start + 1


def parse_understanding(text: str) -> ClipUnderstanding:
    d = _extract_obj(text)
    if d is None:
        return ClipUnderstanding()
    moments: list[ClipMoment] = []
    for m in d.get("moments") or []:
        if not isinstance(m, dict):
            continue
        try:
            t = float(m.get("t", 0.0))
        except (TypeError, ValueError):
            t = 0.0
        moments.append(ClipMoment(t=t, text=str(m.get("happening", m.get("text", "")))))
    return ClipUnderstanding(summary=str(d.get("summary", "")), moments=moments)


# --- grounding + OCR: a per-frame, SINGLE-image pass (boxes need single-image context to be
# accurate, and OCR attributed to one frame). Kept separate from the multi-frame narrative pass. ---


def build_ground_prompt() -> str:
    """Single-image prompt: tight boxes for the main subjects + any on-screen text. Text goes in a
    string field, never a box (a text-as-box request made the model hallucinate boxes)."""
    return (
        "Look at this single image. Find the MAIN visible subjects (people, animals, vehicles, "
        "prominent objects) and give a tight bounding box for each in PIXEL coordinates of THIS "
        "image as [x1,y1,x2,y2] with a top-left origin. Separately, transcribe any readable "
        "on-screen TEXT (signs, captions, place names). Reply with ONLY a JSON object: "
        '{"objects":[{"label":"<what it is>","bbox_2d":[x1,y1,x2,y2]}],'
        '"ocr":"<all readable text joined by spaces, empty string if there is none>"}. '
        "Do NOT put text in a bounding box — text belongs only in the ocr string. "
        "Start your reply with { and end with }."
    )


def _coerce_box(box) -> list[float] | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        return [float(v) for v in box]
    except (TypeError, ValueError):
        return None


def _ocr_str(v) -> str:
    """Coerce an ocr field to a string: join a list with spaces (a disobedient model may return a
    list), keep a string as-is, drop empties — never leak a Python list repr into the prompt."""
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v) if v else ""


def parse_grounding(text: str) -> tuple[list[GroundedObject], str]:
    """Parse a single-frame grounding+OCR reply -> (objects with RAW pixel boxes, ocr string).
    Accepts the wrapped {"objects":[...],"ocr":"..."} shape or a bare [...] array of boxes. A
    wrapper is only honoured when its "objects" is a list — otherwise a bare-array element that
    happens to carry an "ocr"/"objects" key would be mistaken for the wrapper and drop every box."""
    d = _extract_obj(text, keys=("objects", "ocr"))
    if d is not None and isinstance(d.get("objects"), list):
        raw, ocr = d["objects"], _ocr_str(d.get("ocr"))
    else:
        arr = _extract_array(text)
        if arr is not None:
            raw, ocr = arr, ""
        elif d is not None:
            return [], _ocr_str(d.get("ocr"))   # a wrapper with ocr but no usable object list
        else:
            return [], ""
    objs: list[GroundedObject] = []
    for o in raw:
        if not isinstance(o, dict):
            continue
        box = _coerce_box(o.get("bbox_2d") or o.get("box") or o.get("bbox"))
        if box is None:
            continue
        objs.append(GroundedObject(label=str(o.get("label", "")), box=box))
    return objs, ocr


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def normalize_objects(objs: list[GroundedObject], frame_w: int, frame_h: int) -> list[GroundedObject]:
    """Divide pixel boxes by the frame's own dimensions -> normalized [0,1], clamp overshoot, and
    drop zero-area boxes. Qwen2.5-VL emits boxes in the input frame's pixel space (verified live)."""
    out: list[GroundedObject] = []
    if frame_w <= 0 or frame_h <= 0:
        return out
    for o in objs:
        x1, y1, x2, y2 = o.box
        nx1, nx2 = sorted((_clamp01(x1 / frame_w), _clamp01(x2 / frame_w)))
        ny1, ny2 = sorted((_clamp01(y1 / frame_h), _clamp01(y2 / frame_h)))
        if nx2 - nx1 <= 0 or ny2 - ny1 <= 0:
            continue
        out.append(GroundedObject(label=o.label, box=[nx1, ny1, nx2, ny2]))
    return out


# --- photos: a still gets a one-line caption (here) + the grounding+OCR pass (build_ground_prompt) ---


def build_photo_prompt() -> str:
    """Single-image prompt for a photo's one-line caption (who/what/where, the feeling)."""
    return (
        "Describe this single photo in ONE concise sentence: who or what is in it, where, and the "
        'feeling. Reply with ONLY a JSON object: {"caption":"<one sentence>"}. '
        "Start your reply with { and end with }."
    )


def parse_caption(text: str) -> str:
    """Pull the caption string from a photo-caption reply; fall back to the first non-empty line."""
    d = _extract_obj(text, keys=("caption",))
    if d is not None:
        return str(d.get("caption") or "").strip()
    stripped = text.strip()
    return stripped.splitlines()[0].strip() if stripped else ""


def _default_run(prompt: str) -> str:
    from composerv.analyze.backends.claude_cli import claude_read

    return claude_read(prompt)


def understand_clip(
    proxy_path: str,
    duration_s: float,
    run: Callable[[str], str] | None = None,
    target_frames: int = 16,
    frames_dir: str | None = None,
    retries: int = 1,
) -> ClipUnderstanding:
    """Sample full-coverage frames across the clip and get a temporal understanding."""
    from composerv.index.frames import sample_frames

    run = run or _default_run
    frames_dir = frames_dir or tempfile.mkdtemp(prefix="cv_vframes_")
    # spread ~target_frames across the WHOLE duration (never just the first seconds)
    fps = min(1.0, target_frames / duration_s) if duration_s and duration_s > 0 else 1.0
    frames = sample_frames(proxy_path, frames_dir, fps=fps)
    specs = [(f.src_pts_s, f.image_path) for f in frames]
    prompt = build_video_prompt(specs)
    result = ClipUnderstanding()
    for _ in range(retries + 1):
        result = parse_understanding(run(prompt))
        if result.summary or result.moments:
            return result
    return result
