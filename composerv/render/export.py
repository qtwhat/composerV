"""Export a reel to a LITE MP4 (a small, shareable review file).

The composition references the uniform proxies, so this is a quick downscale-and-mux, not a
finish: the same audio mix (music + ducking), the same fade-to-black ending, and the date
stamp burned onto the picture. The Final Cut handoff stays the FCPXML (originals); this MP4 is
for watching/sharing now.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence

from composerv.render.preview.composition import (
    build_audio_mix,
    build_composition,
    build_video_composition,
)

LITE_PRESET = "AVAssetExportPreset1280x720"  # match the 1280x720 reframe canvas (was 960x540)

# AVAssetExportSessionStatus: 0 unknown, 1 waiting, 2 exporting, 3 completed, 4 failed, 5 cancelled
_DONE = (3, 4, 5)


def attach_reframe(clips, store, *, canvas_aspect=16 / 9, fps=30):
    """For each non-gap clip whose DISPLAY aspect != 16:9, compute (or reuse the cached) subject
    track for the WHOLE clip, slice it to [in, out], and attach a per-segment crop ramp as
    `c["reframe_path"]`. Clips already at 16:9 are left untouched (they fill the canvas already).

    Subject hints come from the store: per-moment object boxes (the in-window subject) and the
    named family gallery (so the crop prefers a known person). Mutates and returns `clips`."""
    from composerv.reframe.detect import compute_track
    from composerv.reframe.path import crop_path
    from composerv.reframe.track import slice_track
    from composerv.render.preview.composition import oriented_size

    eps = 0.02
    for c in clips:
        if c.get("kind") == "gap":
            continue
        f, in_s, out_s = c["file"], float(c["in"]), float(c["out"])
        w, h = oriented_size(f)
        if abs(w / h - canvas_aspect) <= eps:
            continue  # already fills 16:9
        person_boxes, target = None, None
        if store:
            pb = []
            for m in store.get_clip_moments_rich(f):
                if m.objects and len(m.objects[0].box) == 4:
                    b = m.objects[0].box  # normalized [x1,y1,x2,y2]
                    pb.append((float(m.t), ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)))
            person_boxes = pb or None
            gallery = dict(store.named_gallery())
            for pid in store.clip_person_ids(f):
                if pid in gallery:
                    target = gallery[pid]
                    break
        full = store.get_reframe_track(f) if store else []
        if not full:
            from composerv.index.probe import probe_media
            dur = probe_media(f).duration_s
            full = compute_track(
                f, 0.0, dur, person_boxes=person_boxes, target_centroid=target,
                progress=lambda i, n: print(f"\rreframe {f.split('/')[-1]}: {i}/{n}", end=""))
            if store:
                store.set_reframe_track(f, full)
        # pad=0.0: slice EXACTLY [in,out] rebased to [0, out-in]; pad>0 would shift the crop window
        # ~0.5s off the played content (M-2). crop_path eases from the first in-window sample.
        c["reframe_path"] = crop_path(slice_track(full, in_s, out_s, pad=0.0), (w, h),
                                      canvas_aspect, fps=fps)
    return clips


def export_mp4(
    clips: Sequence[dict],
    fps: int,
    music: dict | None,
    out_path: str,
    *,
    title: str = "",
    tail_s: float = 1.5,
    preset: str = LITE_PRESET,
    timeout_s: float = 1800.0,
    store=None,
) -> str:
    """Render the EDL (clips/fps/music) to an MP4 at out_path. `title` is burned on as a date
    stamp. Blocks until done; raises RuntimeError on failure. Returns out_path."""
    from AVFoundation import AVAssetExportSession, AVFileTypeMPEG4
    from Foundation import NSURL

    if not [c for c in clips if c.get("kind") != "gap"]:
        raise RuntimeError("nothing to export: the reel has no clips")
    comp = build_composition(clips, fps=fps, music=music)
    session = AVAssetExportSession.exportSessionWithAsset_presetName_(comp, preset)
    if session is None:
        raise RuntimeError(f"could not create an export session for preset {preset!r}")

    if os.path.exists(out_path):
        os.remove(out_path)  # AVAssetExportSession refuses to overwrite
    session.setOutputURL_(NSURL.fileURLWithPath_(out_path))
    session.setOutputFileType_(AVFileTypeMPEG4)

    mix = build_audio_mix(comp, music, fps=fps)
    if mix is not None:
        session.setAudioMix_(mix)
    vc_clips = None
    if store is not None:
        attach_reframe(clips, store, fps=fps)
        vc_clips = clips
    vc = build_video_composition(comp, fps=fps, tail_s=tail_s, title=title, clips=vc_clips)
    if vc is not None:
        session.setVideoComposition_(vc)

    session.exportAsynchronouslyWithCompletionHandler_(lambda: None)
    t0 = time.time()
    while session.status() not in _DONE and time.time() - t0 < timeout_s:
        time.sleep(0.1)
    status = session.status()
    if status != 3:
        if status not in _DONE:
            session.cancelExport()
        raise RuntimeError(f"export failed (status {status}): {session.error()}")
    return out_path
