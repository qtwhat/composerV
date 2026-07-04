"""Tunable, resident-model local understanding.

Ties together the speed/quality knobs we measured:
- frame selection: content-aware (scene/shot changes) or uniform, capped count.
- resolution: optional downscale (fewer vision tokens -> faster prefill).
- a RESIDENT model: load the mlx-vlm model once and reuse it across clips (the per-clip
  reload is small, ~3s, but this also avoids it entirely for batch runs).
- robust timestamp grounding (align moments to real frame times, never collapse to 0.0).

`run(prompt, image_paths) -> str` is injectable for tests; default uses the resident model.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable

from composerv.analyze.backends.qwen_mlx import (
    DEFAULT_MODEL,
    _ground_moments,
    build_local_video_prompt,
    build_perframe_prompt,
    parse_perframe,
)
from composerv.analyze.clip_video import (
    ClipMoment,
    ClipUnderstanding,
    build_ground_prompt,
    build_photo_prompt,
    normalize_objects,
    parse_caption,
    parse_grounding,
    parse_understanding,
)
from composerv.clarity.sampling import downscale_frames, select_keyframes

_VLM_CACHE: dict[str, "LocalVLM"] = {}


class LocalVLM:
    """An mlx-vlm model loaded once and reused (avoids reloading 18GB per clip)."""

    def __init__(self, model: str = DEFAULT_MODEL):
        from mlx_vlm import load
        from mlx_vlm.utils import load_config

        self.model_id = model
        self.model, self.processor = load(model)
        self.config = load_config(model)

    def describe(self, prompt: str, image_paths: list[str], max_tokens: int = 1200) -> str:
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        formatted = apply_chat_template(self.processor, self.config, prompt,
                                        num_images=len(image_paths))
        return generate(self.model, self.processor, formatted, image=image_paths,
                        max_tokens=max_tokens, verbose=False).text


def _resident_run(model: str, max_tokens: int) -> Callable[[str, list[str]], str]:
    def run(prompt: str, image_paths: list[str]) -> str:
        if model not in _VLM_CACHE:
            _VLM_CACHE[model] = LocalVLM(model)
        return _VLM_CACHE[model].describe(prompt, image_paths, max_tokens=max_tokens)

    return run


def understand_clip_tunable(
    video_path: str,
    duration_s: float,
    *,
    run: Callable[[str, list[str]], str] | None = None,
    frames_mode: str = "scene",       # "scene" (content-aware) | "uniform"
    max_frames: int = 12,
    min_frames: int = 4,
    max_long_side: int | None = None,  # resolution knob (None = keep proxy resolution)
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1200,
    threshold: float = 0.3,
    frames_dir: str | None = None,
    ground: bool = False,             # also locate the main subjects (normalized boxes) per frame
    ocr: bool = False,                # also transcribe on-screen text (signs/captions) per frame
    max_ground_frames: int = 4,       # cap the per-frame grounding pass (it is one call per frame)
) -> ClipUnderstanding:
    """Select frames (content-aware/uniform, capped), optionally downscale, run the local VLM, and
    return per-frame moments grounded to real timestamps. With ground/ocr, a SECOND single-image
    pass over the first `max_ground_frames` frames attaches grounded boxes + on-screen text."""
    frames_dir = frames_dir or tempfile.mkdtemp(prefix="cv_understand_")
    kfs = select_keyframes(video_path, frames_dir, duration_s, max_frames=max_frames,
                           min_frames=min_frames, threshold=threshold, mode=frames_mode)
    times = [t for t, _p in kfs]
    paths = [p for _t, p in kfs]
    if max_long_side:
        paths = downscale_frames(paths, frames_dir + "_ds", max_long_side)
    specs = list(zip(times, paths))
    prompt = build_local_video_prompt(specs)
    runner = run or _resident_run(model, max_tokens)
    text = runner(prompt, paths)
    u = parse_understanding(text)
    u.moments = _ground_moments(u.moments, times)
    if ground or ocr:
        _attach_grounding(u.moments, paths[:max_ground_frames], runner,
                          want_boxes=ground, want_ocr=ocr)
    return u


def understand_clip_perframe(
    video_path: str,
    duration_s: float,
    *,
    run: Callable[[str, list[str]], str] | None = None,
    frames_mode: str = "scene",       # scene-cut concentrates frames on real change, not B-roll
    max_frames: int = 16,
    min_frames: int = 4,
    max_long_side: int | None = 512,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 120,
    threshold: float = 0.3,
    frames_dir: str | None = None,
    ground: bool = False,             # also locate the main subjects (normalized boxes) per frame
    ocr: bool = False,                # also transcribe on-screen text (signs/captions) per frame
    max_ground_frames: int = 4,       # cap the per-frame grounding pass (one call per frame)
) -> ClipUnderstanding:
    """Per-frame v2 understanding: ONE single-image VLM call per sampled frame, terse-Chinese +
    people-first prompt. This avoids the multi-image repetition collapse the 7B model falls into
    on similar frames (it described frame 1 then copy-pasted generic scenery for the rest, losing
    a woman's smile-at-camera) and preserves expression / gaze / action. Empty / "无" frames are
    dropped; moments carry real frame timestamps. No summary (per-frame has no whole-clip pass).
    With ground/ocr, a grounding pass over the first kept frames attaches boxes + on-screen text."""
    frames_dir = frames_dir or tempfile.mkdtemp(prefix="cv_perframe_")
    kfs = select_keyframes(video_path, frames_dir, duration_s, max_frames=max_frames,
                           min_frames=min_frames, threshold=threshold, mode=frames_mode)
    times = [t for t, _p in kfs]
    paths = [p for _t, p in kfs]
    if max_long_side:
        paths = downscale_frames(paths, frames_dir + "_ds", max_long_side)
    runner = run or _resident_run(model, max_tokens)
    kept: list[tuple[ClipMoment, str]] = []
    for t, path in zip(times, paths):
        text = parse_perframe(runner(build_perframe_prompt(t), [path]))
        if text:
            kept.append((ClipMoment(t=t, text=text), path))
    moments = [m for m, _p in kept]
    if (ground or ocr) and moments:
        _attach_grounding(moments, [p for _m, p in kept[:max_ground_frames]], runner,
                          want_boxes=ground, want_ocr=ocr)
    return ClipUnderstanding(moments=moments)


def understand_photo(
    image_path: str,
    *,
    run: Callable[[str, list[str]], str] | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 400,
    max_long_side: int | None = 448,
    frames_dir: str | None = None,
) -> ClipMoment:
    """Perceive a single photo with the local VLM: a one-line caption + grounded subject boxes +
    on-screen text. Returns one ClipMoment(t=0). Same models as video, just single-image."""
    runner = run or _resident_run(model, max_tokens)
    frame = image_path
    if max_long_side:
        out_dir = (frames_dir or tempfile.mkdtemp(prefix="cv_photo_")) + "_ds"
        frame = downscale_frames([image_path], out_dir, max_long_side)[0]
    caption = parse_caption(runner(build_photo_prompt(), [frame]))
    objs, ocr = parse_grounding(runner(build_ground_prompt(), [frame]))
    if objs:
        w, h = _frame_dims(frame)
        objs = normalize_objects(objs, w, h)
    return ClipMoment(t=0.0, text=caption, ocr=ocr, objects=objs)


def _frame_dims(path: str) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size  # (w, h)


def _attach_grounding(moments, frame_paths, runner, *, want_boxes: bool, want_ocr: bool) -> None:
    """Per-frame single-image pass: locate subjects + read on-screen text, attach to each moment.
    Single-image because Qwen2.5-VL's boxes are accurate per image and OCR stays attributable."""
    prompt = build_ground_prompt()
    for i, path in enumerate(frame_paths):
        if i >= len(moments):
            break
        try:
            objs, ocr_text = parse_grounding(runner(prompt, [path]))
            if want_boxes and objs:
                w, h = _frame_dims(path)
                moments[i].objects = normalize_objects(objs, w, h)
            if want_ocr and ocr_text:
                moments[i].ocr = ocr_text
        except Exception as e:
            # a single bad frame (model OOM, missing/corrupt jpg) degrades to no box/ocr for that
            # frame — it must NEVER discard the narrative moments computed before this pass
            print(f"[grounding] frame {i} ({path}) failed: {e!r}", file=sys.stderr)
            continue
