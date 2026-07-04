"""Emit FCPXML 1.13 from an IntentionList: the one-way handoff to Final Cut Pro.

The IntentionList is the shared contract (same input as the live preview). Here it becomes
a single-spine sequence that references the ORIGINAL source files (not our proxies), so FCP
finishes at full quality. Source-media seconds become integer-frame rationals at the
timeline fps. Beat labels/rationale ride along as markers.

Pure function (no I/O). NOTE: structurally valid FCPXML 1.13, but real FCP-import compliance
must be confirmed against a golden file exported from the user's Final Cut Pro (esp. NTSC
frame rates and the project format); this emits clean integer-fps timing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import quote
from xml.sax.saxutils import quoteattr

from composerv.models import IntentionList


def _t(sec: float, fps: int) -> str:
    """Source-seconds -> FCPXML rational time at the timeline fps (integer-frame)."""
    frames = round(sec * fps)
    return "0s" if frames == 0 else f"{frames}/{fps}s"


def _file_url(path: str) -> str:
    return "file://" + quote(os.path.abspath(path))


def _db(amount: float) -> str:
    """A gain in dB, formatted for FCPXML adjust-volume (-15.0 -> '-15dB')."""
    s = f"{amount:.1f}".rstrip("0").rstrip(".")
    return f"{s}dB"


def intention_to_fcpxml(
    il: IntentionList,
    source_paths: Mapping[str, str],
    *,
    project_name: str = "composerV story",
    event_name: str = "composerV",
    asset_durations: Mapping[str, float] | None = None,
    width: int = 1920,
    height: int = 1080,
) -> str:
    fps = il.timeline_fps
    segs = [s for s in il.segments if s.enabled]

    # one asset resource per unique source, in first-use order; its duration is the largest
    # out-point we reference (a safe lower bound) unless an explicit full duration is given
    order: list[str] = []
    max_out: dict[str, float] = {}
    photo_sources: set[str] = set()
    for s in segs:
        if s.kind in ("clip", "photo") and s.source_id is not None:
            if s.source_id not in max_out:
                order.append(s.source_id)
            max_out[s.source_id] = max(max_out.get(s.source_id, 0.0), s.out_sec or 0.0)
            if s.kind == "photo":
                photo_sources.add(s.source_id)

    asset_id = {sid: f"r{i + 2}" for i, sid in enumerate(order)}  # r1 is the format

    total_s = sum(s.duration_s for s in segs)
    music = il.music
    music_id = f"r{len(order) + 2}" if music is not None else None
    duck = _db(music.duck_db) if music is not None else None
    hi = _db(music.highlight_db) if music is not None else None
    hls = music.highlights if music is not None else []

    res = [f'<format id="r1" name="FFVideoFormat" frameDuration="1/{fps}s" '
           f'width="{width}" height="{height}"/>']
    for sid in order:
        dur = (asset_durations or {}).get(sid, max_out[sid])
        name = os.path.basename(source_paths[sid])
        has_audio = "0" if sid in photo_sources else "1"  # a still photo has no audio track
        res.append(
            f'<asset id={quoteattr(asset_id[sid])} name={quoteattr(name)} start="0s" '
            f'duration="{_t(dur, fps)}" hasVideo="1" hasAudio="{has_audio}" format="r1">'
            f'<media-rep kind="original-media" src={quoteattr(_file_url(source_paths[sid]))}/>'
            f'</asset>'
        )
    if music is not None:
        mname = quoteattr(os.path.basename(music.path))
        res.append(
            f'<asset id={quoteattr(music_id)} name={mname} start="0s" '
            f'duration="{_t(total_s, fps)}" hasVideo="0" hasAudio="1" audioSources="1">'
            f'<media-rep kind="original-media" src={quoteattr(_file_url(music.path))}/>'
            f'</asset>'
        )

    spine: list[str] = []
    offset = 0.0
    music_placed = False
    for s in segs:
        off = _t(offset, fps)
        dur = _t(s.duration_s, fps)
        if s.kind == "gap":
            name = quoteattr(s.label or "gap")
            spine.append(f'<gap name={name} offset="{off}" start="0s" duration="{dur}"/>')
        else:
            name = quoteattr(os.path.basename(source_paths[s.source_id]))
            start = _t(s.in_sec or 0.0, fps)
            # DTD 1.13 asset-clip child order: adjust-volume, then anchored clips, then markers.
            children = ""
            if music is not None and s.kind != "photo":  # a still has no audio to duck/foreground
                # whole-clip static: a clip overlapping any highlight window is foregrounded;
                # the rest stay ducked under the bed. (Preview does the true sub-clip ramp.)
                overlaps = any(h.start_s < offset + s.duration_s and h.end_s > offset for h in hls)
                children += f'<adjust-volume amount="{hi if overlaps else duck}"/>'
            if music is not None and not music_placed:
                if hls:
                    mv = f'<adjust-volume amount="{_db(music.music_duck_db)}"/>'  # bed ducked deeper
                elif music.gain_db:
                    mv = f'<adjust-volume amount="{_db(music.gain_db)}"/>'
                else:
                    mv = ""
                children += (
                    f'<asset-clip ref={quoteattr(music_id)} name={mname} lane="-1" '
                    f'offset="{start}" start="0s" duration="{_t(total_s, fps)}" '
                    f'audioRole="music">{mv}</asset-clip>'
                )
                music_placed = True
            if s.label or s.note:
                mval = quoteattr(s.label or s.note)
                children += (f'<marker start="{start}" duration="1/{fps}s" value={mval}/>')
            spine.append(
                f'<asset-clip ref={quoteattr(asset_id[s.source_id])} name={name} '
                f'offset="{off}" start="{start}" duration="{dur}" '
                f'tcFormat="NDF">{children}</asset-clip>'
            )
        offset += s.duration_s

    total = _t(offset, fps)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE fcpxml>\n'
        '<fcpxml version="1.13">\n'
        f'<resources>{"".join(res)}</resources>\n'
        f'<library><event name={quoteattr(event_name)}>'
        f'<project name={quoteattr(project_name)}>'
        f'<sequence format="r1" duration="{total}" tcStart="0s" tcFormat="NDF">'
        f'<spine>{"".join(spine)}</spine>'
        f'</sequence></project></event></library>\n'
        '</fcpxml>\n'
    )
