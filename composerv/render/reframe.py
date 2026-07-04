"""Focus-aware reframe: crop each shot to a target aspect (e.g. 3:4 vertical), keeping the
subject in frame, no black bars.

Mixed-orientation footage → one chosen output shape. The crop is computed in the clip's OWN
(original) pixel space — that's where the face boxes live and where the real pixels are — then
ffmpeg crops+scales the original per segment, and the existing export muxes the uniform result
(so the audio mix / ducking / fade / date overlay all still apply). Pure halves (clip_focus,
crop_rect) are unit-tested; the ffmpeg + export orchestration is validated on real footage.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence


def clip_focus(bboxes: Sequence[Sequence[float]], src_w: float, src_h: float) -> tuple[float, float] | None:
    """Area-weighted centroid of the face boxes → a normalized (0..1) focus point. Bigger /
    nearer faces pull the focus toward them; a group stays centred-ish. None if no usable box."""
    if not bboxes or src_w <= 0 or src_h <= 0:
        return None
    tot = cx = cy = 0.0
    for b in bboxes:
        x1, y1, x2, y2 = b[0], b[1], b[2], b[3]
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area <= 0:
            continue
        tot += area
        cx += area * (x1 + x2) / 2
        cy += area * (y1 + y2) / 2
    if tot <= 0:
        return None
    return (cx / tot / src_w, cy / tot / src_h)


_PEOPLE = ("person", "people", "man", "woman", "child", "kid", "girl", "boy", "baby", "face")


def focus_from_objects(objects, prefer: tuple[str, ...] = _PEOPLE) -> tuple[float, float] | None:
    """Area-weighted centroid of grounded object boxes (already normalized [0,1]) → a focus point.
    Prefers people boxes when present, else uses all objects. None if there are no usable boxes.
    Lets a photo focus on its subject using the VLM's grounding (no face model needed)."""
    boxes = [o.box for o in objects if any(k in (o.label or "").lower() for k in prefer)]
    if not boxes:
        boxes = [o.box for o in objects]
    tot = cx = cy = 0.0
    for b in boxes:
        if len(b) < 4:
            continue
        x1, y1, x2, y2 = b[0], b[1], b[2], b[3]
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area <= 0:
            continue
        tot += area
        cx += area * (x1 + x2) / 2
        cy += area * (y1 + y2) / 2
    return (cx / tot, cy / tot) if tot > 0 else None


def crop_rect(src_w: int, src_h: int, target_ar: float,
              focus: tuple[float, float] | None = None) -> tuple[int, int, int, int]:
    """Largest target-aspect (w/h) rect that fits in the source, centred on the normalized
    focus and clamped to bounds. focus None → centre. Returns (x, y, w, h) in source pixels."""
    fx, fy = focus if focus else (0.5, 0.5)
    if src_w / src_h >= target_ar:          # source wider than target -> limited by height
        ch = src_h
        cw = int(round(src_h * target_ar))
    else:                                    # source taller -> limited by width
        cw = src_w
        ch = int(round(src_w / target_ar))
    cw, ch = min(cw, src_w), min(ch, src_h)
    x = int(round(fx * src_w - cw / 2))
    y = int(round(fy * src_h - ch / 2))
    x = max(0, min(x, src_w - cw))
    y = max(0, min(y, src_h - ch))
    return (x, y, cw, ch)


def _photo_filter(crop: tuple[int, int, int, int], out_w: int, out_h: int, dur: float,
                  motion: str, fps: int = 30) -> str:
    """ffmpeg -vf for turning a still into a held shot: focus-crop + scale, plus an optional gentle
    Ken Burns (zoompan). 'in' pushes in, 'out' pulls out, anything else holds static."""
    x, y, w, h = crop
    base = f"crop={w}:{h}:{x}:{y}"
    if motion in ("in", "out"):
        frames = max(1, int(round(dur * fps)))
        span = 0.12  # move 12% over the hold
        if motion == "in":
            zexpr = f"min(zoom+{span / frames:.6f},{1.0 + span:.3f})"
        else:
            zexpr = f"if(eq(on,0),{1.0 + span:.3f},max(zoom-{span / frames:.6f},1.0))"
        # upscale first so zoompan samples a big canvas (avoids the classic 1px jitter)
        return (f"{base},scale=3840:-2,zoompan=z='{zexpr}':d={frames}"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':fps={fps}:s={out_w}x{out_h}")
    return f"{base},scale={out_w}:{out_h}"


def photo_to_clip(src: str, crop: tuple[int, int, int, int], out_w: int, out_h: int,
                  dur: float, motion: str, dst: str, fps: int = 30) -> str:
    """Render a still photo into a silent held clip (focus-crop to target shape + Ken Burns)."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", src, "-t", f"{dur}",
         "-r", str(fps), "-vf", _photo_filter(crop, out_w, out_h, dur, motion, fps),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", dst],
        check=True)
    return dst


def reframe_segment(src: str, in_s: float, out_s: float, crop: tuple[int, int, int, int],
                    out_w: int, out_h: int, dst: str) -> str:
    """ffmpeg: take [in_s, out_s] of `src`, crop to `crop`, scale to out_w×out_h, keep audio."""
    x, y, w, h = crop
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{in_s}", "-i", src, "-t", f"{out_s - in_s}",
         "-vf", f"crop={w}:{h}:{x}:{y},scale={out_w}:{out_h}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "16000", "-ac", "1", dst],
        check=True)
    return dst


def export_reframed(intention, store, out_path: str, *, target_ar: float = 0.75,
                    out_w: int = 1080, out_h: int = 1440, title: str = "",
                    workdir: str | None = None) -> str:
    """Crop every shot of `intention` to target_ar (focus on faces, from the ORIGINALS), then
    export the uniform result as one MP4 (music mix + fade + date overlay preserved)."""
    from composerv.models import IntentionList, Segment
    from composerv.render.export import export_mp4
    from composerv.render.preview.edl import intention_to_edl

    workdir = workdir or tempfile.mkdtemp(prefix="cv_reframe_")
    segs = []
    for i, seg in enumerate(intention.segments):
        if seg.kind not in ("clip", "photo") or not seg.source_id:
            continue
        a = store.get_asset(seg.source_id)
        if not a or not a.width or not a.height:
            continue
        dst = os.path.join(workdir, f"seg{i:03d}.mp4")
        try:
            if seg.kind == "photo":
                # focus a still on its subject via the VLM grounding boxes (people first), else faces
                objs = [o for m in store.get_clip_moments_rich(seg.source_id) for o in m.objects]
                focus = focus_from_objects(objs) or clip_focus(
                    [f.bbox for f in store.get_faces(seg.source_id)], a.width, a.height)
                crop = crop_rect(a.width, a.height, target_ar, focus)
                dur = seg.duration_s
                photo_to_clip(seg.source_id, crop, out_w, out_h, dur, seg.motion or "static", dst)
            else:
                focus = clip_focus([f.bbox for f in store.get_faces(seg.source_id)], a.width, a.height)
                crop = crop_rect(a.width, a.height, target_ar, focus)
                reframe_segment(seg.source_id, seg.in_sec, seg.out_sec, crop, out_w, out_h, dst)
                dur = seg.out_sec - seg.in_sec
        except Exception as e:  # a single bad shot (e.g. an undecodable HEIC) must not kill the reel
            print(f"[reframe] skipped {seg.kind} {seg.source_id}: {e!r}", file=sys.stderr)
            continue
        segs.append(Segment(kind="clip", source_id=dst, in_sec=0.0, out_sec=dur, duration_s=dur))

    new_il = IntentionList(story_id="reframe", segments=segs, music=intention.music)
    edl = intention_to_edl(new_il, {s.source_id: s.source_id for s in segs})
    export_mp4(edl["clips"], edl.get("fps", 30), edl.get("music"), out_path, title=title,
               tail_s=1.5)
    return out_path
