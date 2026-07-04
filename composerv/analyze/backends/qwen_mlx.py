"""Local TRUE-video understanding via mlx-vlm + Qwen2.5-VL (private, no cloud).

mlx-vlm 0.6.3's native video reader (`video_generate`, "numpy reader") is beta and
produced garbage frames in testing (the model saw "an abstract pattern"). The model's
single-/multi-IMAGE path works correctly, so we extract full-coverage frames OURSELVES
(our proven sampler, exact timestamps) and feed them as an ordered image list to
`mlx_vlm.generate`. The model reasons over the sequence (what happens over time) — the
same way Qwen-VL handles video internally — entirely on-device. Pixels never leave the
machine: the privacy path for family footage.

build_local_video_prompt / understand_clip_local pure parts are tested; the model run is
validated live.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections.abc import Callable

from composerv.analyze.clip_video import ClipUnderstanding, parse_understanding

DEFAULT_MODEL = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"


def build_local_video_prompt(frames: list[tuple[float, str]]) -> str:
    listing = "\n".join(f"frame {i + 1}: t={t:.1f}s" for i, (t, _p) in enumerate(frames))
    n = len(frames)
    return (
        f"You are given {n} frames sampled IN ORDER across one short video clip (their timestamps "
        "are listed below, same order as the images). Reason about what HAPPENS over time: actions, "
        "movement, events, how the scene and subjects change. Do not describe a single frame in "
        f"isolation.\n\nTimestamps in order:\n{listing}\n\n"
        "Give ONE moment per frame, in order, and make each moment describe what is NEW or has "
        "CHANGED since the previous frame (do not repeat the same description). Reply with ONLY a "
        'JSON object: {"summary":"<2-4 sentences on what happens across the whole clip over time>",'
        '"moments":[{"t":<the frame timestamp>,"happening":"<what is new/changing then>"}]}. '
        "Start your reply with { and end with }."
    )


def build_perframe_prompt(t: float) -> str:
    """One terse-Chinese, people-first description for a SINGLE frame at second `t`. Per-frame
    (one image per call) avoids the multi-image repetition collapse the 7B model falls into on
    similar frames; the rules flip priority to people + expression + gaze so a warm human moment
    is never lost under generic scenery, and ban the model's hollow filler words."""
    return (
        f"这是视频第 {t:.0f} 秒的一帧。用一句中文描述这帧画面。\n\n"
        "规则：\n"
        "1. 画面中有人时，必须写：谁在做什么、表情（笑/大笑/皱眉/专注/平静等）、"
        "视线（看镜头/看左边/低头等）。不写风景。\n"
        "2. 画面中没有人时，也必须描述：写场景主体加一个具体细节（颜色、运动方向、物体）。"
        '禁止只回答"无"或留空。\n'
        "3. 禁止使用：宁静、氛围、美丽、serene、tranquil。只写肉眼可见的事实。\n"
        "4. 长度按内容定：有人或有动作时，把表情、视线、动作写全（这种镜头最重要），"
        "不约束字数、越详细越好；纯场景一句带过，15字内即可。不堆砌、不写废话。\n\n"
        f'只输出这一行：{{"t":{t:.0f},"d":"..."}}'
    )


def parse_perframe(text: str) -> str:
    """Pull the description out of a per-frame reply: a {"t":..,"d":".."} object OR a bare line.
    Strips code fences; maps the model's empty / "无" non-answers to "" so the caller can drop
    contentless frames."""
    s = text.strip().strip("`").strip()
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        try:
            obj = json.loads(s[a:b + 1])
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and "d" in obj:
            s = str(obj["d"])
    s = s.strip().strip('"').strip()
    return "" if s in ("无", "无。", "none", "None") else s


def _ground_moments(moments, times: list[float]):
    """Assign each moment the real timestamp of its frame, BY POSITION, and keep only as many
    moments as there are frames.

    We feed frames in order at known times, so position i corresponds to times[i]. Small models
    sometimes emit a different number of lines than frames; aligning by position (and truncating
    any extras) means the timestamps are always real, instead of collapsing to 0.0 on a mismatch.
    """
    n = min(len(moments), len(times))
    out = moments[:n]
    for i in range(n):
        out[i].t = times[i]
    return out


def _extract_generation(out: str) -> str:
    """mlx_vlm.generate echoes the full templated prompt to stdout before the model's reply,
    then a trailing stats block. Return ONLY the model's generation. If the assistant marker
    is absent (e.g. an injected fake), return the text unchanged."""
    marker = "<|im_start|>assistant"
    idx = out.rfind(marker)
    if idx != -1:
        out = out[idx + len(marker):]
    cut = out.find("\n==========")  # drop the trailing "Prompt: N tokens ..." stats block
    if cut != -1:
        out = out[:cut]
    return out.strip()


def _run_generate(image_paths: list[str], prompt: str, model: str, max_tokens: int, timeout: int) -> str:
    """Shell mlx-vlm's image generate over an ordered frame list; return the model's generation
    (echoed prompt + trailing stats stripped)."""
    cmd = [
        sys.executable, "-m", "mlx_vlm.generate",
        "--model", model,
        "--image", *image_paths,
        "--prompt", prompt,
        "--max-tokens", str(max_tokens),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return _extract_generation(proc.stdout)
    except (subprocess.TimeoutExpired, OSError):
        return ""


def understand_clip_local(
    video_path: str,
    duration_s: float = 0.0,
    model: str = DEFAULT_MODEL,
    target_frames: int = 16,
    max_tokens: int = 1200,
    timeout: int = 900,
    frames_dir: str | None = None,
    run: Callable[..., str] | None = None,
) -> ClipUnderstanding:
    """Local, on-device clip understanding -> ClipUnderstanding (same shape as the Claude
    path). Frames are extracted by us (exact timestamps) and fed to the local VLM."""
    from composerv.index.frames import sample_frames

    runner = run or _run_generate
    frames_dir = frames_dir or tempfile.mkdtemp(prefix="cv_qframes_")
    fps = min(1.0, target_frames / duration_s) if duration_s and duration_s > 0 else 1.0
    frames = sample_frames(video_path, frames_dir, fps=fps)
    specs = [(f.src_pts_s, f.image_path) for f in frames]
    prompt = build_local_video_prompt(specs)
    text = runner([p for _t, p in specs], prompt, model, max_tokens, timeout)
    u = parse_understanding(text)
    u.moments = _ground_moments(u.moments, [t for t, _p in specs])  # positional timestamp grounding
    return u
