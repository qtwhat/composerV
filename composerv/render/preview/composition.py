"""Build an AVComposition (a virtual, zero-render timeline) from an edit list.

This is the heart of the live preview. An AVComposition references source media by URL +
time mapping; playing it composites cuts in real time with NO transcode. Editing the
story rebuilds the composition and swaps the player item, which is metadata work, not a
render.

We use ONE video track and ONE audio track and append into them left-to-right. This is
safe only because composerV's proxies are uniform CFR (same fps/resolution/codec); track
reuse is what avoids AVFoundation's track-count cost cliff and the cross-frame-rate seek
freeze. Garbage in (mixed/VFR proxies) would reintroduce those failure modes.

EDL format (a thin, domain-decoupled list of dicts):
    {"kind": "clip", "file": <path>, "in": <sec>, "out": <sec>}
    {"kind": "gap", "duration": <sec>}
"""

from __future__ import annotations

from collections.abc import Sequence

from AVFoundation import (
    AVMediaTypeAudio,
    AVMediaTypeVideo,
    AVMutableAudioMix,
    AVMutableAudioMixInputParameters,
    AVMutableComposition,
    AVURLAsset,
)
from CoreMedia import (
    CMTimeAdd,
    CMTimeGetSeconds,
    CMTimeMake,
    CMTimeRangeMake,
    kCMTimeZero,
)
from Foundation import NSURL

_INVALID_TRACK_ID = 0  # kCMPersistentTrackID_Invalid


def _db_to_linear(db: float) -> float:
    """A dB gain as an AVFoundation linear volume (0dB -> 1.0, -6dB -> ~0.5)."""
    return 10.0 ** (db / 20.0)


def _frames(seconds: float, fps: int) -> int:
    return int(round(seconds * fps))


def _t(seconds: float, fps: int):
    """A CMTime expressed in integer frames at the timeline fps (frame-accurate boundary)."""
    return CMTimeMake(_frames(seconds, fps), fps)


# ---------------------------------------------------------------------------
# Pure layout + transform helpers (unit-tested; no AVFoundation dependency)
# ---------------------------------------------------------------------------

def clip_layout(edl, fps: int):
    """[(start_s, dur_s, clip)] with a FRAME-SNAPPED integer-frame cursor mirroring
    build_composition (raw-float accumulation drifts ~frames over many clips)."""
    out, cf = [], 0
    for item in edl:
        if item.get("kind") == "gap":
            cf += _frames(float(item["duration"]), fps)
            continue
        df = _frames(float(item["out"]) - float(item["in"]), fps)
        out.append((cf / fps, df / fps, item))
        cf += df
    return out


def fit_transform(src_w, src_h, canvas_w, canvas_h):
    """(scale, tx, ty) that letter/pillarboxes src into canvas, centered. No sign flip."""
    scale = min(canvas_w / src_w, canvas_h / src_h)
    return (scale, (canvas_w - src_w * scale) / 2, (canvas_h - src_h * scale) / 2)


def fill_transform(crop_rect, canvas_w, canvas_h):
    """(scale, tx, ty) that maps a top-left source crop rect onto the canvas.
    TOP-LEFT origin, +y DOWN — matches AVFoundation layer-instruction transform space.
    No sign flip: ty = -y * scale (moves content up when y > 0)."""
    x, y, w, h = crop_rect
    scale = canvas_w / w
    return (scale, -x * scale, -y * scale)


# ---------------------------------------------------------------------------
# Live orientation helpers (AVFoundation; validated by Task 6 live gate)
# ---------------------------------------------------------------------------

def _video_track(path):
    from AVFoundation import AVMediaTypeVideo, AVURLAsset
    from Foundation import NSURL
    a = AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(path), None)
    vt = a.tracksWithMediaType_(AVMediaTypeVideo)
    return vt[0] if vt else None


def oriented_size(path):
    """(w, h) of the clip in its DISPLAY orientation (applies preferredTransform)."""
    from Quartz import CGRectApplyAffineTransform, CGRectMake
    vt = _video_track(path)
    if vt is None:
        return (1280, 720)
    ns = vt.naturalSize()
    r = CGRectApplyAffineTransform(CGRectMake(0, 0, ns.width, ns.height), vt.preferredTransform())
    return (abs(r.size.width), abs(r.size.height))


def orient_transform(path):
    """CGAffineTransform that rotates the source upright (preferredTransform + origin-normalize).
    The origin-normalize translate is required: without it a 90-degree rotation renders all-black
    (verified in workflow wf_297d4bd2). Identity for unrotated sources."""
    from Quartz import (CGAffineTransformConcat, CGAffineTransformIdentity,
                        CGAffineTransformMakeTranslation, CGRectApplyAffineTransform, CGRectMake)
    vt = _video_track(path)
    if vt is None:
        return CGAffineTransformIdentity
    ns = vt.naturalSize()
    pref = vt.preferredTransform()
    r = CGRectApplyAffineTransform(CGRectMake(0, 0, ns.width, ns.height), pref)
    norm = CGAffineTransformMakeTranslation(-r.origin.x, -r.origin.y)
    return CGAffineTransformConcat(pref, norm)


def build_composition(edl: Sequence[dict], fps: int = 30, music: dict | None = None) -> AVMutableComposition:
    comp = AVMutableComposition.composition()
    vtrack = comp.addMutableTrackWithMediaType_preferredTrackID_(AVMediaTypeVideo, _INVALID_TRACK_ID)
    atrack = comp.addMutableTrackWithMediaType_preferredTrackID_(AVMediaTypeAudio, _INVALID_TRACK_ID)

    cursor = kCMTimeZero
    for item in edl:
        if item.get("kind") == "gap":
            dur = _t(item["duration"], fps)
            empty = CMTimeRangeMake(cursor, dur)
            vtrack.insertEmptyTimeRange_(empty)
            atrack.insertEmptyTimeRange_(empty)
            cursor = CMTimeAdd(cursor, dur)
            continue

        in_sec = float(item["in"])
        out_sec = float(item["out"])
        dur = _t(out_sec - in_sec, fps)
        src_range = CMTimeRangeMake(_t(in_sec, fps), dur)

        url = NSURL.fileURLWithPath_(item["file"])
        asset = AVURLAsset.URLAssetWithURL_options_(url, None)

        vsrc = asset.tracksWithMediaType_(AVMediaTypeVideo)
        if vsrc:
            ok, err = vtrack.insertTimeRange_ofTrack_atTime_error_(src_range, vsrc[0], cursor, None)
            if not ok:
                raise RuntimeError(f"video insert failed for {item['file']}: {err}")

        asrc = asset.tracksWithMediaType_(AVMediaTypeAudio)
        if asrc:
            atrack.insertTimeRange_ofTrack_atTime_error_(src_range, asrc[0], cursor, None)
        else:
            # keep the audio timeline aligned with video even if a clip has no audio
            atrack.insertEmptyTimeRange_(CMTimeRangeMake(cursor, dur))

        cursor = CMTimeAdd(cursor, dur)

    if music:
        _add_music_track(comp, music)

    return comp


def _add_music_track(comp, music: dict) -> None:
    """Lay the music file across a second audio track, trimmed to the video timeline.

    Shorter music just ends early; longer music is trimmed. Levels are applied later by
    build_audio_mix (volume is a playback property, not part of the composition)."""
    total = comp.duration()
    mtrack = comp.addMutableTrackWithMediaType_preferredTrackID_(AVMediaTypeAudio, _INVALID_TRACK_ID)
    url = NSURL.fileURLWithPath_(music["file"])
    asset = AVURLAsset.URLAssetWithURL_options_(url, None)
    asrc = asset.tracksWithMediaType_(AVMediaTypeAudio)
    if not asrc:
        import sys
        print(f"[music] no audio track in {music.get('file')!r}; reel will have no music bed",
              file=sys.stderr)
        return
    src_dur = asset.duration()
    use = src_dur if CMTimeGetSeconds(src_dur) <= CMTimeGetSeconds(total) else total
    rng = CMTimeRangeMake(kCMTimeZero, use)
    mtrack.insertTimeRange_ofTrack_atTime_error_(rng, asrc[0], kCMTimeZero, None)


def _apply_ramp(params, v0: float, v1: float, t0: float, t1: float, fps: int) -> None:
    """A linear volume ramp over [t0,t1], snapped to whole frames. Skipped if it rounds to
    zero length. Adjacent ramps on a track must not OVERLAP (AVFoundation raises and aborts
    mix construction) — callers guarantee spacing via _merged_windows."""
    f0, f1 = round(t0 * fps), round(t1 * fps)
    if f1 <= f0:
        return
    params.setVolumeRampFromStartVolume_toEndVolume_timeRange_(
        v0, v1, CMTimeRangeMake(CMTimeMake(f0, fps), CMTimeMake(f1 - f0, fps))
    )


def _merged_windows(highlights, total_s: float, *, default_music_db: float, default_clip_db: float):
    """Normalize highlight dicts to in-bounds, level-resolved windows and MERGE any whose
    edge-ramp skirts would overlap. Defensive: build_audio_mix must never crash even if fed
    hand-authored overlapping windows, so the merge lives here too, not only in projection."""
    norm = []
    for h in highlights or []:
        s, e = max(0.0, float(h["start"])), min(total_s, float(h["end"]))
        if e <= s:
            continue
        cdb, mdb = h.get("clip_db"), h.get("music_duck_db")
        norm.append({
            "s": s, "e": e, "r": float(h.get("ramp", 0.25) or 0.25),
            "clip_db": default_clip_db if cdb is None else cdb,
            "music_db": default_music_db if mdb is None else mdb,
        })
    norm.sort(key=lambda w: w["s"])
    merged = []
    for w in norm:
        if merged and w["s"] < merged[-1]["e"] + 2 * max(merged[-1]["r"], w["r"]):
            merged[-1]["e"] = max(merged[-1]["e"], w["e"])  # fold in; keep first window's levels
        else:
            merged.append(w)
    return merged


def build_audio_mix(comp, music: dict | None, fps: int = 30):
    """An AVAudioMix that plays music-first with dynamic highlight ducking. Baseline: clip
    audio at duck_db under the music at gain_db. Inside each highlight window the clip rises
    to highlight_db and the music dips to music_duck_db, with ramp_s fades at both edges;
    the music fades out at the end of the timeline. Returns None when there is no music bed.

    Attach the result to the AVPlayerItem (item.setAudioMix_); it is a playback property, so
    it is rebuilt cheaply alongside the composition on every edit."""
    if not music:
        return None
    atracks = comp.tracksWithMediaType_(AVMediaTypeAudio)
    if len(atracks) < 2:
        return None
    clip_track, music_track = atracks[0], atracks[1]
    total_s = CMTimeGetSeconds(comp.duration())

    gain = _db_to_linear(music.get("gain_db", 0.0))
    duck = _db_to_linear(music["duck_db"])

    clip_p = AVMutableAudioMixInputParameters.audioMixInputParametersWithTrack_(clip_track)
    clip_p.setVolume_atTime_(duck, kCMTimeZero)  # baseline anchor
    music_p = AVMutableAudioMixInputParameters.audioMixInputParametersWithTrack_(music_track)
    music_p.setVolume_atTime_(gain, kCMTimeZero)

    windows = _merged_windows(
        music.get("highlights", []), total_s,
        default_music_db=music.get("music_duck_db", -18.0),
        default_clip_db=music.get("highlight_db", 0.0),
    )
    # track the level each track is left at, and where the last ramp ends, so the closing
    # fade starts from the right value and never overlaps a highlight ramp.
    clip_level, music_level, last_ramp_end = duck, gain, 0.0
    for w in windows:
        clip_hi, mus_lo = _db_to_linear(w["clip_db"]), _db_to_linear(w["music_db"])
        s, e, r = w["s"], w["e"], w["r"]
        a0, b1 = max(0.0, s - r), min(total_s, e + r)
        _apply_ramp(clip_p, duck, clip_hi, a0, s, fps)   # entry: lift the clip
        _apply_ramp(music_p, gain, mus_lo, a0, s, fps)   # entry: dip the music
        _apply_ramp(clip_p, clip_hi, duck, e, b1, fps)   # exit: clip back to duck
        _apply_ramp(music_p, mus_lo, gain, e, b1, fps)   # exit: music back to gain
        if round(b1 * fps) > round(e * fps):             # exit ramp existed -> back to baseline
            clip_level, music_level, last_ramp_end = duck, gain, max(last_ramp_end, b1)
        else:                                            # ran to the end -> held at hi/lo
            clip_level, music_level, last_ramp_end = clip_hi, mus_lo, max(last_ramp_end, s)

    # closing fade: bring BOTH the music and the clip's own audio to silence so the reel
    # resolves instead of cutting dead — even when it ends mid-conversation.
    fade = float(music.get("fade_out_s", 0.0) or 0.0)
    if fade > 0:
        fstart = max(total_s - fade, last_ramp_end)
        _apply_ramp(music_p, music_level, 0.0, fstart, total_s, fps)
        _apply_ramp(clip_p, clip_level, 0.0, fstart, total_s, fps)

    mix = AVMutableAudioMix.audioMix()
    mix.setInputParameters_([clip_p, music_p])
    return mix


def build_video_composition(comp, fps: int = 30, tail_s: float = 1.5, title: str = "", clips=None):
    """An AVVideoComposition that fades the picture to black over the last tail_s (so the reel
    ends on a fade-out, not a hard cut) and, when `title` is given, burns that text onto the
    picture as a date stamp (bottom-left). Returns None if there is no video.

    When `clips` is given (the EDL list with optional reframe_path entries), applies a per-segment
    layer transform: FILL ramp for clips with reframe_path (subject-track crop), FIT-centered for
    aspect-mismatched clips without reframe_path (fixes left-align), and upright rotation for any
    source with a non-identity preferredTransform. Canvas is fixed at 1280x720 in this mode.

    Attach via item.setVideoComposition_ for preview (call WITHOUT a title — playback can't use
    an animation tool) or session.setVideoComposition_ for export (WITH a title)."""
    from AVFoundation import (
        AVMutableVideoComposition,
        AVMutableVideoCompositionInstruction,
        AVMutableVideoCompositionLayerInstruction,
    )

    vtracks = comp.tracksWithMediaType_(AVMediaTypeVideo)
    if not vtracks:
        return None
    vtrack = vtracks[0]
    nat = vtrack.naturalSize()
    if nat.width <= 0 or nat.height <= 0:  # empty/broken composition -> no video comp
        return None
    total = comp.duration()
    total_s = CMTimeGetSeconds(total)

    layer = AVMutableVideoCompositionLayerInstruction.videoCompositionLayerInstructionWithAssetTrack_(vtrack)
    tail = min(float(tail_s), total_s)
    if tail > 0:
        f0, f1 = round((total_s - tail) * fps), round(total_s * fps)
        if f1 > f0:
            layer.setOpacityRampFromStartOpacity_toEndOpacity_timeRange_(
                1.0, 0.0, CMTimeRangeMake(CMTimeMake(f0, fps), CMTimeMake(f1 - f0, fps)))

    # Per-segment transform (clips given) — FILL ramp for reframe_path clips, FIT-centered for
    # mismatched clips. Both live on the same layer as the tail opacity ramp (independent properties).
    if clips is not None:
        from Quartz import CGAffineTransformConcat, CGAffineTransformMake, CGSizeMake
        canvas_w, canvas_h = 1280, 720
        size = CGSizeMake(canvas_w, canvas_h)

        def _aff(t3):
            s, tx, ty = t3
            return CGAffineTransformMake(s, 0.0, 0.0, s, tx, ty)

        for start_s, dur_s, clip in clip_layout(clips, fps):
            orient = orient_transform(clip["file"])
            seg_f0 = _frames(start_s, fps)
            seg_f1 = seg_f0 + _frames(dur_s, fps)
            rp = clip.get("reframe_path")
            if rp:
                # Set an anchor transform at the segment's first frame to avoid previous-clip leak.
                layer.setTransform_atTime_(
                    CGAffineTransformConcat(orient, _aff(fill_transform(rp[0][1], canvas_w, canvas_h))),
                    CMTimeMake(seg_f0, fps))
                for i in range(len(rp) - 1):
                    (t0, r0), (t1, r1) = rp[i], rp[i + 1]
                    f0 = max(seg_f0, min(seg_f1, seg_f0 + _frames(t0 - rp[0][0], fps)))
                    f1 = max(seg_f0, min(seg_f1, seg_f0 + _frames(t1 - rp[0][0], fps)))
                    if f1 > f0:
                        a0 = CGAffineTransformConcat(orient, _aff(fill_transform(r0, canvas_w, canvas_h)))
                        a1 = CGAffineTransformConcat(orient, _aff(fill_transform(r1, canvas_w, canvas_h)))
                        layer.setTransformRampFromStartTransform_toEndTransform_timeRange_(
                            a0, a1, CMTimeRangeMake(CMTimeMake(f0, fps), CMTimeMake(f1 - f0, fps)))
            else:
                ow, oh = oriented_size(clip["file"])
                layer.setTransform_atTime_(
                    CGAffineTransformConcat(orient, _aff(fit_transform(ow, oh, canvas_w, canvas_h))),
                    CMTimeMake(seg_f0, fps))
    else:
        size = nat  # legacy path: use the track's natural size unchanged

    inst = AVMutableVideoCompositionInstruction.videoCompositionInstruction()
    inst.setTimeRange_(CMTimeRangeMake(kCMTimeZero, total))
    inst.setLayerInstructions_([layer])

    vc = AVMutableVideoComposition.videoComposition()
    vc.setInstructions_([inst])
    vc.setFrameDuration_(CMTimeMake(1, fps))
    vc.setRenderSize_(size)
    if title:
        _attach_title_overlay(vc, size, title, total_s)
    return vc


def make_date_stamp_layer(text: str, font_size: float, margin: float):
    """A bold POP-ART date stamp as a CALayer, anchored `margin` px from the bottom-left corner:
    a vivid yellow block with a thick black border, heavy black text, a red comic offset-echo,
    and a slight tilt. Drawn into a bitmap with AppKit (renders CJK + the block + shadow
    correctly) and set as the layer's CONTENTS — a CATextLayer silently drops text in the
    offline export render context, whereas an image layer composites fine. Shared by the export
    overlay and the live-preview overlay."""
    import math

    from AppKit import (
        NSAttributedString,
        NSBezierPath,
        NSBitmapImageRep,
        NSColor,
        NSDeviceRGBColorSpace,
        NSFont,
        NSFontAttributeName,
        NSForegroundColorAttributeName,
        NSGraphicsContext,
        NSMakePoint,
        NSMakeRect,
        NSMakeSize,
        NSShadow,
        NSShadowAttributeName,
        NSStrokeColorAttributeName,
        NSStrokeWidthAttributeName,
    )
    from Quartz import CALayer, CATransform3DMakeRotation, CGRectMake

    yellow = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.84, 0.0, 1.0)
    black = NSColor.blackColor()
    red = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.93, 0.16, 0.22, 1.0)

    font = NSFont.fontWithName_size_("PingFangSC-Semibold", font_size) or NSFont.boldSystemFontOfSize_(font_size)
    echo = NSShadow.alloc().init()          # hard red offset = comic mis-registration
    echo.setShadowColor_(red)
    echo.setShadowBlurRadius_(0.0)
    echo.setShadowOffset_(NSMakeSize(font_size * 0.07, -font_size * 0.07))
    astr = NSAttributedString.alloc().initWithString_attributes_(text, {
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: black,
        NSStrokeColorAttributeName: black,
        NSStrokeWidthAttributeName: -4.0,   # negative = fill + stroke (extra heft)
        NSShadowAttributeName: echo,
    })

    sz = astr.size()
    bx, by = font_size * 0.45, font_size * 0.28   # block padding around the text
    border = max(4.0, font_size * 0.10)
    pad = int(math.ceil(border + font_size * 0.12))  # bitmap margin for border + echo
    bw, bh = int(math.ceil(sz.width + 2 * bx)), int(math.ceil(sz.height + 2 * by))
    w, h = bw + 2 * pad, bh + 2 * pad

    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bitmapFormat_bytesPerRow_bitsPerPixel_(  # noqa: E501
        None, w, h, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)

    block = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(pad, pad, bw, bh), font_size * 0.18, font_size * 0.18)
    yellow.setFill()
    block.fill()
    black.setStroke()
    block.setLineWidth_(border)
    block.stroke()
    astr.drawAtPoint_(NSMakePoint(pad + bx, pad + by))

    NSGraphicsContext.restoreGraphicsState()

    layer = CALayer.layer()
    layer.setContents_(rep.CGImage())
    layer.setFrame_(CGRectMake(margin, margin, w, h))
    layer.setTransform_(CATransform3DMakeRotation(-3.0 * math.pi / 180.0, 0.0, 0.0, 1.0))
    return layer


def _intro_fade_animation(total_s: float, intro_s: float = 3.0, fade_s: float = 0.8):
    """A keyframe opacity animation for the date stamp: full for the first (intro_s - fade_s),
    fade to 0 by intro_s, then gone. Synced to the composition timeline (for the export tool)."""
    from AVFoundation import AVCoreAnimationBeginTimeAtZero
    from Quartz import CAKeyframeAnimation, kCAFillModeBoth

    total = max(total_s, intro_s + 0.01)
    hold = max(0.0, intro_s - fade_s)
    anim = CAKeyframeAnimation.animationWithKeyPath_("opacity")
    anim.setValues_([1.0, 1.0, 0.0, 0.0])
    anim.setKeyTimes_([0.0, hold / total, intro_s / total, 1.0])
    anim.setDuration_(total)
    anim.setBeginTime_(AVCoreAnimationBeginTimeAtZero)  # = timeline 0
    anim.setRemovedOnCompletion_(False)
    anim.setFillMode_(kCAFillModeBoth)
    return anim


def _attach_title_overlay(vc, size, title: str, total_s: float) -> None:
    """Burn `title` onto the picture as a bottom-left date stamp that shows once at the start and
    fades out (~3s), so it doesn't cover the picture for the whole reel. Export only (playback
    cannot use an animation tool)."""
    from AVFoundation import AVVideoCompositionCoreAnimationTool
    from Quartz import CALayer, CGRectMake

    w, h = size.width, size.height
    parent = CALayer.layer()
    parent.setFrame_(CGRectMake(0, 0, w, h))
    video = CALayer.layer()
    video.setFrame_(CGRectMake(0, 0, w, h))
    parent.addSublayer_(video)
    stamp = make_date_stamp_layer(title, max(40.0, h * 0.07), h * 0.045)
    stamp.addAnimation_forKey_(_intro_fade_animation(total_s), "introFade")
    parent.addSublayer_(stamp)

    tool = AVVideoCompositionCoreAnimationTool.videoCompositionCoreAnimationToolWithPostProcessingAsVideoLayer_inLayer_(
        video, parent)
    vc.setAnimationTool_(tool)


def composition_seconds(comp) -> float:
    return CMTimeGetSeconds(comp.duration())


def video_source_ranges(comp) -> list[tuple[float, float]]:
    """For each non-empty video segment, (source_start_seconds, source_duration_seconds).

    Lets tests assert the edit is frame-accurate: the composition references exactly the
    requested in/out of each source clip.
    """
    tracks = comp.tracksWithMediaType_(AVMediaTypeVideo)
    if not tracks:
        return []
    out: list[tuple[float, float]] = []
    for seg in tracks[0].segments():
        if seg.isEmpty():
            continue
        src = seg.timeMapping().source  # CMTimeRange of the source clip
        out.append((CMTimeGetSeconds(src.start), CMTimeGetSeconds(src.duration)))
    return out
